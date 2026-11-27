import WebSocket from 'ws';
import { ACPProcess } from './acp.js';
import { log } from './log.js';

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class Connector {
  constructor(options) {
    this.server = options.server;
    this.token = options.token;
    this.agentId = options.agent_id;
    this.name = options.name || options.agent_id;
    this.permissionMode = options.permission_mode || 'native';
    this.reconnectDelay = options.reconnect_delay || 5000;
    this.agent = new ACPProcess(options.command, options.agent_args, options.cwd, this.permissionMode, options.agent_env);
    // 不挂 onNotification，即不向服务端转发 ACP 的 session/update 流式碎片。
    // 本场景只需要 Agent 的最终回复，服务端也会丢弃这些碎片（agent_gateway._message_loop 里 update 分支是 pass），
    // 一个长回复能产生数百条无效 WS 消息。将来要做控制台流式打字效果时，
    // 恢复这里的赋值，并在服务端把 update 写入对应 message_id 的记录即可。
    this.ws = null;
    this.queue = [];
    this.messageResolver = null;
    this.promptAbort = null;
    this.stopped = false;
    this.fatal = '';
    this.authed = false;
  }

  async run() {
    while (!this.stopped && !this.fatal) {
      try {
        await this.agent.start();
        await this._connect();
      } catch (err) {
        if (this.stopped) break;
        if (this.fatal) {
          log(`${this.agentId} 接入失败: ${this.fatal}`);
          break;
        }
        log(`${this.agentId} 连接异常: ${err.message}`);
      } finally {
        await this.agent.stop().catch(() => {});
      }
      if (!this.stopped && !this.fatal) await sleep(this.reconnectDelay);
    }
  }

  async _connect() {
    const url = new URL(`${String(this.server).replace(/\/$/, '')}/api/v1/agent/ws`);
    url.searchParams.set('token', this.token);
    url.searchParams.set('agent_id', this.agentId);
    const ws = new WebSocket(url.toString(), { handshakeTimeout: 10000 });
    this.ws = ws;
    const abort = new AbortController();
    this.promptAbort = abort;
    this.authed = false;
    log(`连接 Agent Gateway: ${this.server} agent_id=${this.agentId}`);

    // 关键：message 监听必须在握手前挂上。服务端 accept 后立即校验 token，
    // auth_fail 可能在握手 Promise 还没 resolve 时就到达，此时若无监听器该帧会被直接丢弃，
    // 表现是「密钥错误却只报连接断开并无限重连」。
    let rejectMessageFailure;
    const messageFailure = new Promise((_, reject) => {
      rejectMessageFailure = reject;
    });
    const onMessage = (raw) => {
      this._onMessage(raw.toString()).catch(rejectMessageFailure);
    };
    ws.on('message', onMessage);

    try {
      await Promise.race([
        new Promise((resolve, reject) => {
          ws.once('open', resolve);
          ws.once('error', reject);
          ws.once('close', () => reject(new Error('WebSocket 连接失败')));
        }),
        messageFailure,
      ]);
      // 握手完成不代表鉴权通过，「连接成功」等收到 auth_ok 再打
      // register：向服务端自报身份与能力。连接归属其实已由 URL 上的 token + agent_id 确定，
      // 服务端当前对 register 只 continue（见 agent_gateway._message_loop），
      // 保留它是为了协议完整性，以及将来按 capabilities 做能力协商
      // （delegation 委派 / permissions 权限确认 / nativeSessionHistory 会话历史复用）。
      await this.send({
        type: 'register',
        name: this.name,
        agent_id: this.agentId,
        capabilities: { delegation: false, permissions: true, nativeSessionHistory: false },
      });

      this._startPromptWorker(abort.signal);

      await Promise.race([
        new Promise((resolve, reject) => {
          ws.once('close', resolve);
          ws.once('error', reject);
        }),
        messageFailure,
      ]);
      log(`${this.agentId} 连接断开`);
    } finally {
      ws.off('message', onMessage);
      abort.abort();
      this.queue = [];
      if (this.messageResolver) {
        this.messageResolver(undefined);
        this.messageResolver = null;
      }
      this.promptAbort = null;
      if (this.ws) {
        this.ws.close();
        this.ws = null;
      }
    }
  }

  async _onMessage(raw) {
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      return;
    }
    if (!data || typeof data !== 'object') return;
    const type = data.type;
    if (type === 'auth_ok') {
      if (!this.authed) {
        this.authed = true;
        log(`${this.agentId} 连接成功`);
      }
      return;
    }
    if (type === 'auth_fail') {
      // 鉴权类失败（密钥无效、配额不足、该 Agent 已在其他机器接入）都不可能靠重试解决，
      // 一律标记致命并退出该 Agent 的循环，避免无限重连和反复拉起子进程
      this.fatal = data.message || `鉴权失败（${data.error || 'unknown'}）`;
      throw new Error(this.fatal);
    }
    if (type === 'ping') {
      await this.send({ type: 'pong' });
      return;
    }
    if (type === 'message') {
      this._enqueue(data);
      return;
    }
    if (type === 'cancel') {
      await this.agent.cancelSession();
      return;
    }
    log(`忽略服务端消息: ${type}`);
  }

  async send(payload) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      // 回复与错误是有业务含义的消息，静默丢弃会让用户以为 Agent 没回，这里留一条日志
      if (payload.type === 'response' || payload.type === 'error') {
        log(`${this.agentId} 连接不可用，${payload.type} 未发送`);
      }
      return;
    }
    this.ws.send(JSON.stringify(payload));
  }

  _enqueue(message) {
    this.queue.push(message);
    if (this.messageResolver) {
      const resolve = this.messageResolver;
      this.messageResolver = null;
      resolve(this.queue.shift());
    }
  }

  async _waitForMessage(signal) {
    if (this.queue.length) return this.queue.shift();
    return new Promise((resolve) => {
      const cleanup = () => {
        signal.removeEventListener('abort', onAbort);
        if (this.messageResolver === wrapped) this.messageResolver = null;
      };
      const onAbort = () => {
        cleanup();
        resolve(undefined);
      };
      const wrapped = (item) => {
        cleanup();
        resolve(item);
      };
      signal.addEventListener('abort', onAbort, { once: true });
      this.messageResolver = wrapped;
    });
  }

  async _startPromptWorker(signal) {
    while (!signal.aborted && !this.stopped) {
      const message = await this._waitForMessage(signal);
      if (!message) continue;
      try {
        await this._handlePrompt(message);
      } catch (err) {
        log(`${this.agentId} 任务失败: ${err.message}`);
        await this.send({
          type: 'error',
          text: err.message || 'Agent 执行失败',
          role: 'error',
          device_id: message.device_id || '',
          message_id: message.message_id,
        }).catch(() => {});
      }
    }
  }

  async _handlePrompt(message) {
    const text = String(message.text || '');
    if (!text) return;
    const sessionId = await this.agent.ensureSession();
    const messageId = message.message_id ?? Date.now();
    const deviceId = String(message.device_id || '');
    this.agent.activeMessageId = messageId;
    this.agent.activeDeviceId = deviceId;
    this.agent.currentText = [];
    // 换行换成空格再截，否则日志会被拆成多行难以阅读
    const ask = text.replace(/[\r\n]+/g, ' ');
    log(`${this.agentId} 收到任务: message_id=${messageId} device_id=${deviceId || '-'} text=${ask.slice(0, 10)}${ask.length > 10 ? '...' : ''}`);

    const result = await this.agent.request('session/prompt', {
      sessionId,
      prompt: [{ type: 'text', text }],
    });

    let finalText = this.agent.currentText.join('').trim();
    if (!finalText) {
      const blocks = Array.isArray(result?.content) ? result.content : result?.content ? [result.content] : [];
      for (const block of blocks) {
        if (block && block.type === 'text') finalText += String(block.text || '');
      }
      finalText = finalText.trim();
    }
    const stopReason = result?.stopReason || 'end_turn';
    if (stopReason !== 'end_turn') {
      log(`${this.agentId} 任务未完成: message_id=${messageId} stopReason=${stopReason}`);
      await this.send({
        type: 'error',
        text: `Agent 未完成: ${stopReason}`,
        role: 'error',
        device_id: deviceId,
        message_id: messageId,
      });
      return;
    }
    const answer = finalText.replace(/[\r\n]+/g, ' ');
    log(`${this.agentId} 回复完成: message_id=${messageId} response=${answer.slice(0, 10)}${answer.length > 10 ? '...' : ''}`);
    await this.send({
      type: 'response',
      text: finalText,
      role: 'agent',
      device_id: deviceId,
      message_id: messageId,
    });
  }

  stop() {
    if (this.stopped) return;
    this.stopped = true;
    log(`${this.agentId} 收到停止信号`);
    if (this.promptAbort) this.promptAbort.abort();
    if (this.messageResolver) {
      const resolve = this.messageResolver;
      this.messageResolver = null;
      resolve(undefined);
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}
