import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const IS_WIN = process.platform === 'win32';

export const AGENT_ORDER = [
  'openclaw',
  'hermes',
  'codex',
  'qwen',
  'opencode',
  'qoder',
  'minimax',
  'kimi',
  'codebuddy',
  'claude',
  'deepseek',
  'pi',
  'acp',
];

export const AGENT_LABELS = {
  acp: 'ACP',
  claude: 'Claude Code',
  codebuddy: 'CodeBuddy',
  codex: 'Codex',
  deepseek: 'DeepSeek',
  hermes: 'Hermes',
  kimi: 'Kimi Code',
  minimax: 'MiniMax Code',
  opencode: 'OpenCode',
  openclaw: 'OpenClaw',
  pi: 'Pi',
  qoder: 'Qoder',
  qwen: 'Qwen Code',
};

function isExecutable(file) {
  if (!file) return false;
  try {
    const st = fs.statSync(file);
    if (!st.isFile()) return false;
    if (IS_WIN) return true;
    fs.accessSync(file, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function exeCandidates(command) {
  if (command.includes('/') || command.includes('\\')) return [command];
  if (IS_WIN) return [command, `${command}.exe`, `${command}.cmd`, `${command}.bat`];
  return [command];
}

function unique(values) {
  return Array.from(new Set(values.filter(Boolean)));
}

function nvmDirs() {
  const root = process.env.NVM_DIR || path.join(os.homedir(), '.nvm');
  const versions = path.join(root, 'versions', 'node');
  try {
    return fs
      .readdirSync(versions)
      .sort()
      .reverse()
      .map((name) => path.join(versions, name, 'bin'));
  } catch {
    return [];
  }
}

function searchDirs() {
  const home = os.homedir();
  const dirs = (process.env.PATH || '').split(path.delimiter).filter(Boolean);
  if (IS_WIN) {
    return unique([
      process.env.APPDATA ? path.join(process.env.APPDATA, 'npm') : '',
      process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, 'npm') : '',
      process.env.ProgramFiles ? path.join(process.env.ProgramFiles, 'nodejs') : '',
      process.env['ProgramFiles(x86)'] ? path.join(process.env['ProgramFiles(x86)'], 'nodejs') : '',
      ...dirs,
    ]);
  }
  return unique([
    ...nvmDirs(),
    path.join(home, '.npm-global', 'bin'),
    path.join(home, '.bun', 'bin'),
    path.join(home, '.local', 'bin'),
    '/opt/homebrew/bin',
    '/usr/local/bin',
    ...dirs,
  ]);
}

function commandFallback(command) {
  try {
    const out = execFileSync(IS_WIN ? 'where' : 'which', [command], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    const first = out.split(/\r?\n/).find(Boolean);
    return first && isExecutable(first) ? first : '';
  } catch {
    return '';
  }
}

function findExecutable(command, envNames = []) {
  const envList = Array.isArray(envNames) ? envNames : [envNames].filter(Boolean);
  for (const envName of envList) {
    const envPath = process.env[envName];
    if (isExecutable(envPath)) return envPath;
  }
  for (const dir of searchDirs()) {
    for (const candidate of exeCandidates(command)) {
      const full = path.join(dir, candidate);
      if (isExecutable(full)) return full;
    }
  }
  if (!command.includes('/') && !command.includes('\\')) {
    return commandFallback(command);
  }
  return '';
}

function makeTarget(key, command, agentArgs, env, extraArgs) {
  return {
    agent_id: key,
    name: AGENT_LABELS[key] || key,
    command,
    agent_args: [...agentArgs, ...(extraArgs || [])],
    agent_env: env || {},
  };
}

function localAgent(command, envNames, baseArgs, permissionMode, fullArgs = []) {
  const file = findExecutable(command, envNames);
  if (!file) return null;
  const args = [...baseArgs];
  if (permissionMode === 'full' && fullArgs.length) args.push(...fullArgs);
  return { command: file, args, env: {} };
}

function adapterAgent(parentCommand, parentEnv, adapterCommand, adapterEnv, pkg, parentEnvKeys = []) {
  const parent = findExecutable(parentCommand, parentEnv);
  const env = {};
  if (parent) {
    for (const key of parentEnvKeys) env[key] = parent;
  }
  const adapter = findExecutable(adapterCommand, adapterEnv);
  if (adapter) return { command: adapter, args: [], env };
  const npx = findExecutable('npx', ['NPX_BIN']);
  if (parent && npx) return { command: npx, args: ['-y', pkg], env };
  return null;
}

function toWsUrl(rawUrl) {
  if (rawUrl.startsWith('https://')) return `wss://${rawUrl.slice('https://'.length)}`;
  if (rawUrl.startsWith('http://')) return `ws://${rawUrl.slice('http://'.length)}`;
  return rawUrl;
}

function openclawAgent() {
  const file = findExecutable('openclaw', ['OPENCLAW_ACP_BIN', 'OPENCLAW_BIN', 'OPENCLAW_PATH']);
  if (!file) return null;
  const args = ['acp', '--verbose'];
  const rawUrl = process.env.OPENCLAW_GATEWAY_URL || process.env.OPENCLAW_WS_URL || process.env.OPENCLAW_BASE_URL || '';
  if (rawUrl) args.push('--url', toWsUrl(rawUrl));
  const token = process.env.OPENCLAW_GATEWAY_TOKEN || process.env.OPENCLAW_TOKEN || '';
  if (token) args.push('--token', token);
  return { command: file, args, env: {} };
}

export function buildAgent(key, permissionMode) {
  if (key === 'openclaw') return openclawAgent();
  if (key === 'hermes') return localAgent('hermes', ['HERMES_BIN'], ['acp', '--accept-hooks'], permissionMode);
  if (key === 'qwen') return localAgent('qwen', ['QWEN_CODE_BIN', 'QWEN_BIN'], ['--acp'], permissionMode);
  if (key === 'opencode') return localAgent('opencode', ['OPENCODE_BIN', 'OPENCODE_PATH'], ['acp'], permissionMode);
  if (key === 'qoder') {
    return localAgent(
      'qodercli',
      ['QODERCLI_PATH', 'QODER_CLI_PATH'],
      ['--acp'],
      permissionMode,
      ['--dangerously-skip-permissions'],
    );
  }
  if (key === 'minimax') return localAgent('mcode', ['MINIMAX_CODE_BIN', 'MINIMAX_BIN'], ['acp'], permissionMode);
  if (key === 'kimi') return localAgent('kimi', ['KIMI_CODE_BIN', 'KIMI_BIN'], ['acp'], permissionMode);
  if (key === 'codebuddy') {
    return localAgent(
      'codebuddy',
      ['CODEBUDDY_BIN', 'CODEBUDDY_PATH'],
      ['--acp'],
      permissionMode,
      ['--dangerously-skip-permissions'],
    );
  }
  if (key === 'deepseek') {
    return localAgent('dsh', ['DEEPSEEK_HARNESS_BIN', 'DSH_BIN'], ['--profile', 'acp'], permissionMode);
  }
  if (key === 'codex') {
    return adapterAgent(
      'codex',
      ['CODEX_PATH', 'CODEX_BIN'],
      'codex-acp',
      ['CODEX_ACP_BIN', 'CODEX_ACP_PATH'],
      '@agentclientprotocol/codex-acp@latest',
      ['CODEX_PATH'],
    );
  }
  if (key === 'claude') {
    return adapterAgent(
      'claude',
      ['CLAUDE_CODE_EXECUTABLE', 'CLAUDE_BIN'],
      'claude-agent-acp',
      ['CLAUDE_CODE_ACP_BIN', 'CLAUDE_AGENT_ACP_BIN'],
      '@agentclientprotocol/claude-agent-acp@latest',
      ['CLAUDE_CODE_EXECUTABLE'],
    );
  }
  if (key === 'pi') {
    return adapterAgent('pi', ['PI_BIN', 'PI_COMMAND'], 'pi-acp', ['PI_ACP_BIN'], 'pi-acp@latest', ['PI_COMMAND', 'PI_BIN']);
  }
  if (key === 'acp') {
    const command = process.env.ACP_COMMAND;
    if (!command) return null;
    const args = process.env.ACP_ARGS ? process.env.ACP_ARGS.match(/(?:[^\s"]+|"[^"]*")+/g)?.map((x) => x.replace(/^"|"$/g, '')) || [] : [];
    return { command, args, env: {} };
  }
  return null;
}

export function collectTargets(permissionMode = 'native', extraArgs = []) {
  return AGENT_ORDER.map((key) => {
    const built = buildAgent(key, permissionMode);
    return built ? makeTarget(key, built.command, built.args, built.env, extraArgs) : null;
  }).filter(Boolean);
}
