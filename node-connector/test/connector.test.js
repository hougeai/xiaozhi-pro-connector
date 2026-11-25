import test from 'node:test';
import assert from 'node:assert/strict';
import { WebSocketServer } from 'ws';
import { Connector } from '../src/connector.js';

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
    console.log(JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { stopReason: 'end_turn', content: [{ type: 'text', text: 'ok' }] } }));
  }
});
`;

test('Connector bridges Gateway messages to ACP and replies', async () => {
  const received = [];
  const done = new Promise((resolve) => {
    const wss = new WebSocketServer({ host: '127.0.0.1', port: 0 });
    wss.on('listening', async () => {
      const port = wss.address().port;
      const connector = new Connector({
        server: `ws://127.0.0.1:${port}`,
        token: 't',
        agent_id: 'test',
        name: 'T',
        cwd: process.cwd(),
        permission_mode: 'native',
        command: process.execPath,
        agent_args: ['-e', FAKE_ACP],
        agent_env: {},
        reconnect_delay: 100,
      });
      const run = connector.run();
      wss.on('connection', (ws) => {
        ws.on('message', (raw) => {
          const data = JSON.parse(raw.toString());
          received.push(data);
          if (data.type === 'register') {
            ws.send(JSON.stringify({ type: 'message', text: 'go', device_id: 'd1', message_id: 1 }));
          }
          if (data.type === 'response') {
            connector.stop();
            wss.close();
            run.catch(() => {});
            resolve(data);
          }
        });
      });
    });
  });

  const response = await done;
  assert.equal(response.type, 'response');
  assert.equal(response.text, 'ok');
  assert.ok(received.some((x) => x.type === 'register'));
});
