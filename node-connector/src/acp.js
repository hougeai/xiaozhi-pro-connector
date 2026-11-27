import { spawn } from 'node:child_process';
import readline from 'node:readline';
import { log } from './log.js';
import { VERSION } from './version.js';

const PROTOCOL_VERSION = 1;
const STDERR_LIMIT = 12000;
const SESSION_TIMEOUT = 60000;
const AGENT_DEBUG = process.env.AGENT_DEBUG === '1';

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class ACPProcess {
  constructor(command, args, cwd, permissionMode, env = {}) {
    this.command = command;
    this.args = args || [];
    this.cwd = cwd;
    this.permissionMode = permissionMode || 'native';
    this.env = env || {};
    this.proc = null;
    this.rl = null;
    this.pending = new Map();
    this.nextId = 1;
    this.onNotification = null;
    this.sessionId = null;
    this.activeMessageId = null;
    this.activeDeviceId = '';
    this.currentText = [];
    this.stderrTail = '';
  }

  async start() {
    if (this.proc && this.proc.exitCode === null && this.proc.signalCode === null) {
      return;
    }
    log(`启动 ACP Agent: ${this.command} ${this.args.join(' ')}`.trim());
    const env = { ...process.env, ...this.env };
    const childArgs = [...this.args];
    const options = {
      cwd: this.cwd,
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
    };
    if (process.platform === 'win32' && /\.(cmd|bat)$/i.test(this.command)) {
      options.shell = true;
      options.windowsVerbatimArguments = true;
    }
    this.proc = spawn(this.command, childArgs, options);
    this.proc.on('exit', () => {
      this.rejectPending(new Error(`ACP Agent 已退出: ${this.stderrTail.slice(-500) || 'unknown'}`));
      try {
        this.rl?.close();
      } catch {
        // ignore
      }
      this.rl = null;
      this.proc = null;
    });
    this.rl = readline.createInterface({ input: this.proc.stdout, crlfDelay: Infinity });
    this.rl.on('line', (line) => this._handleLine(line));
    this.proc.stderr.on('data', (chunk) => {
      const text = chunk.toString('utf8');
      this.stderrTail = (this.stderrTail + text).slice(-STDERR_LIMIT);
      if (AGENT_DEBUG) {
        for (const line of text.split(/\r?\n/).filter(Boolean)) log(`agent: ${line}`);
      }
    });

    const result = await this.request(
      'initialize',
      {
        protocolVersion: PROTOCOL_VERSION,
        clientCapabilities: { elicitation: { form: {} } },
        clientInfo: { name: 'xiaozhi-pro-connector', version: VERSION },
      },
      15000,
    );
    if (result?.protocolVersion !== PROTOCOL_VERSION) {
      throw new Error(`ACP 协议版本不兼容: ${result?.protocolVersion}`);
    }
    // 每次新进程都要重建 session，否则重连后会沿用上一个已退出进程的 sessionId
    this.sessionId = null;
    this.currentText = [];
    this.activeMessageId = null;
    this.activeDeviceId = '';
    log('ACP initialize 完成');
  }

  async ensureSession() {
    if (!this.sessionId) {
      const session = await this.request('session/new', { cwd: this.cwd, mcpServers: [] }, SESSION_TIMEOUT);
      this.sessionId = session.sessionId;
      log(`ACP session 创建完成: ${this.sessionId}`);
    }
    return this.sessionId;
  }

  async request(method, params, timeout) {
    const id = this.nextId++;
    const key = String(id);
    const promise = new Promise((resolve, reject) => {
      if (timeout) {
        const timer = setTimeout(() => {
          this.pending.delete(key);
          reject(new Error(`ACP 请求超时: ${method}`));
        }, timeout);
        this.pending.set(key, { resolve, reject, timer });
      } else {
        // 不传 timeout 表示无限等待（如 session/prompt），进程退出时由 rejectPending 兜底
        this.pending.set(key, { resolve, reject, timer: null });
      }
    });

    try {
      this._write({ jsonrpc: '2.0', id, method, params: params || {} });
      return await promise;
    } finally {
      const pending = this.pending.get(key);
      if (pending) {
        if (pending.timer) clearTimeout(pending.timer);
        this.pending.delete(key);
      }
    }
  }

  async notify(method, params) {
    this._write({ jsonrpc: '2.0', method, params: params || {} });
  }

  async respond(id, result) {
    this._write({ jsonrpc: '2.0', id, result });
  }

  async cancelSession() {
    if (!this.sessionId) return;
    await this.notify('session/cancel', { sessionId: this.sessionId });
  }

  async stop() {
    const proc = this.proc;
    if (!proc) return;
    const exit = new Promise((resolve) => proc.once('exit', resolve));
    if (proc.exitCode === null && proc.signalCode === null) {
      proc.kill('SIGTERM');
    }
    const raced = await Promise.race([exit, sleep(3000)]);
    if (raced === undefined && proc.exitCode === null && proc.signalCode === null) {
      proc.kill('SIGKILL');
    }
    await exit;
    try {
      this.rl?.close();
    } catch {
      // ignore
    }
    this.rl = null;
    this.proc = null;
    this.rejectPending(new Error('ACP Agent 已停止'));
  }

  _write(message) {
    if (!this.proc?.stdin?.writable) {
      throw new Error('ACP Agent 未启动');
    }
    this.proc.stdin.write(`${JSON.stringify(message)}\n`);
  }

  _handleLine(line) {
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      return;
    }
    try {
      this._handleMessage(message);
    } catch (err) {
      // Agent 回的消息格式意外时只记日志，绝不能让进程崩掉
      log(`ACP 消息处理异常: ${err.message}`);
    }
  }

  _handleMessage(message) {
    const id = message.id === undefined || message.id === null ? null : String(message.id);
    if (id && (Object.prototype.hasOwnProperty.call(message, 'result') || Object.prototype.hasOwnProperty.call(message, 'error'))) {
      const pending = this.pending.get(id);
      if (!pending) return;
      clearTimeout(pending.timer);
      this.pending.delete(id);
      if (message.error) {
        pending.reject(new Error(message.error.message || 'ACP 请求失败'));
      } else {
        pending.resolve(message.result || {});
      }
      return;
    }

    if (message.method === 'session/update') {
      this._handleUpdate(message.params || {});
    } else if (message.method === 'session/request_permission') {
      this._handlePermission(message.id, message.params || {}).catch((err) => {
        log(`权限响应失败: ${err.message}`);
      });
    }
  }

  _extractText(update) {
    const type = update.sessionUpdate;
    if (type !== 'agent_message_chunk' && type !== 'agent_thought_chunk') return '';
    // ACP 协议中 content 是单个 ContentBlock，兼容个别 Agent 发数组的情况
    const blocks = Array.isArray(update.content) ? update.content : [update.content];
    const parts = [];
    for (const block of blocks) {
      if (block && block.type === 'text') parts.push(String(block.text || ''));
    }
    const text = parts.join('');
    if (type === 'agent_message_chunk') this.currentText.push(text);
    return text;
  }

  _handleUpdate(params) {
    const update = params.update || {};
    // _extractText 必须无条件调用，它负责把 agent_message_chunk 累积进 currentText，
    // 最终回复就靠它；只有「转发流式碎片」这一步是可选的
    const text = this._extractText(update);
    if (!text || !this.onNotification) return;
    Promise.resolve(
      this.onNotification({
        type: 'update',
        text,
        role: 'agent',
        device_id: this.activeDeviceId,
        message_id: this.activeMessageId,
      }),
    ).catch((err) => log(`发送 update 失败: ${err.message}`));
  }

  async _handlePermission(id, params) {
    if (id === undefined || id === null) return;
    const options = params.options || [];
    if (this.permissionMode === 'full') {
      for (const option of options) {
        if (option && (option.kind === 'allow_once' || option.kind === 'allow_always')) {
          log(`自动允许权限: ${option.optionId}`);
          await this.respond(id, { outcome: { outcome: 'selected', optionId: option.optionId } });
          return;
        }
      }
    }
    log('拒绝权限请求');
    await this.respond(id, { outcome: { outcome: 'rejected' } });
  }

  rejectPending(error) {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }
}
