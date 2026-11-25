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
    this.agent.onNotification = (payload) => this.send(payload);
    this.ws = null;
    this.queue = [];
    this.messageResolver = null;
    this.promptAbort = null;
    this.stopped = false;
  }

  async run() {
    while (!this.stopped) {
      try {
        await this.agent.start();
        await this._connect();
      } catch (err) {
        if (this.stopped) break;
        log(`${this.agentId} 连接异常: ${err.message}`);
      } finally {
        await this.agent.stop().catch(() => {});
      }
      if (!this.stopped) await sleep(this.reconnectDelay);
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
    log(`连接 Agent Gateway: ${this.server} agent_id=${this.agentId}`);

    try {
      await new Promise((resolve, reject) => {
        const cleanup = () => {
          ws.off('open', onOpen);
          ws.off('error', onError);
          ws.off('close', onClose);
        };
        const onOpen = () => {
          cleanup();
          resolve();
        };
        const onError = (err) => {
          cleanup();
          reject(err);
        };
        const onClose = () => {
          cleanup();
          reject(new Error('WebSocket 连接失败'));
        };
        ws.once('open', onOpen);
        ws.once('error', onError);
        ws.once('close', onClose);
      });
      log(`${this.agentId} 连接成功`);

      await this.send({
        type: 'register',
        name: this.name,
        agent_id: this.agentId,
        capabilities: { delegation: false, permissions: true, nativeSessionHistory: false },
      });

      this._startPromptWorker(abort.signal);

      await new Promise((resolve, reject) => {
        const onMessage = (raw) => {
          this._onMessage(raw.toString()).catch(reject);
        };
        const onClose = () => {
          ws.off('message', onMessage);
          ws.off('error', onError);
          resolve();
        };
        const onError = (err) => {
          ws.off('message', onMessage);
          ws.off('close', onClose);
          reject(err);
        };
        ws.on('message', onMessage);
        ws.on('close', onClose);
        ws.on('error', onError);
        if (this.stopped) ws.close();
      });
      log(`${this.agentId} 连接断开`);
    } finally {
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
    if (type === 'auth_ok') return;
    if (type === 'auth_fail') throw new Error(data.message || '鉴权失败');
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
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
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
    log(`${this.agentId} 收到任务: message_id=${messageId} device_id=${deviceId || '-'}`);

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
    const preview = finalText.replace(/\s+/g, ' ').slice(0, 10);
    log(`${this.agentId} 回复完成: message_id=${messageId} response=${preview}...`);
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
