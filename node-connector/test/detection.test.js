import test from 'node:test';
import assert from 'node:assert/strict';
import { buildAgent } from '../src/detection.js';

test('buildAgent uses ACP_COMMAND for generic ACP agents', () => {
  const prevCommand = process.env.ACP_COMMAND;
  const prevArgs = process.env.ACP_ARGS;
  process.env.ACP_COMMAND = '/bin/echo';
  process.env.ACP_ARGS = '--version';
  try {
    const agent = buildAgent('acp', 'native');
    assert.equal(agent.command, '/bin/echo');
    assert.deepEqual(agent.args, ['--version']);
  } finally {
    if (prevCommand === undefined) delete process.env.ACP_COMMAND;
    else process.env.ACP_COMMAND = prevCommand;
    if (prevArgs === undefined) delete process.env.ACP_ARGS;
    else process.env.ACP_ARGS = prevArgs;
  }
});
