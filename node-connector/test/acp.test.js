import test from 'node:test';
import assert from 'node:assert/strict';
import { ACPProcess } from '../src/acp.js';

const FAKE_ACP = `
const readline = require('node:readline');
const rl = readline.createInterface({ input: process.stdin });
rl.on('line', (line) => {
  const msg = JSON.parse(line);
  if (msg.method === 'initialize') {
    console.log(JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { protocolVersion: 1 } }));
  } else if (msg.method === 'session/new') {
    console.log(JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { sessionId: 's1' } }));
  } else if (msg.method === 'session/prompt') {
    console.log(JSON.stringify({
      jsonrpc: '2.0',
      method: 'session/update',
      params: { update: { sessionUpdate: 'agent_message_chunk', content: [{ type: 'text', text: 'hi ' }] } },
    }));
    console.log(JSON.stringify({
      jsonrpc: '2.0',
      method: 'session/update',
      params: { update: { sessionUpdate: 'agent_message_chunk', content: [{ type: 'text', text: 'there' }] } },
    }));
    console.log(JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { stopReason: 'end_turn', content: [] } }));
  }
});
`;

test('ACPProcess handles initialize, session/new, and prompt updates', async () => {
  const agent = new ACPProcess(process.execPath, ['-e', FAKE_ACP], process.cwd(), 'native', {});
  try {
    await agent.start();
    const sessionId = await agent.ensureSession();
    assert.equal(sessionId, 's1');
    const notifications = [];
    agent.onNotification = async (payload) => notifications.push(payload);
    agent.currentText = [];
    const result = await agent.request('session/prompt', { sessionId, prompt: [{ type: 'text', text: 'go' }] }, 5000);
    assert.equal(result.stopReason, 'end_turn');
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(agent.currentText.join(''), 'hi there');
    assert.equal(notifications.length, 2);
  } finally {
    await agent.stop();
  }
});
