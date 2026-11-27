import { AGENT_LABELS, AGENT_ORDER, collectTargets } from './detection.js';
import { Connector } from './connector.js';
import { selectTargets } from './config.js';
import { log } from './log.js';
import { VERSION } from './version.js';
const SERVER = 'wss://api.qlcloud.top/xzp';

const HELP = `Usage: xiaozhi-pro-connector [options]

Options:
  --token <token>          小智Pro API Key，也可用 XIAOZHI_PRO_TOKEN
  --agents <a,b>           本次只接入指定 Agent（如 claude,codex），配合 --save 写入配置
  --save                   与 --agents 同用，把选择结果存到配置文件
  --reset                  清空已保存的 Agent 选择并重新选择
  -- <args>                分隔符之后的参数原样透传给 Agent
  -h, --help               显示帮助
  -v, --version            显示版本

首次运行会列出探测到的 Agent 让你选择，结果写入 ~/.xiaozhi-pro/connector.json，
之后直接启动即按配置接入；nohup 等无终端场景需已有配置或用 --agents。
`;

const BOOL_FLAGS = { '--save': 'save', '--reset': 'reset', '--help': 'help', '--version': 'version', '-h': 'help', '-v': 'version' };
const VALUE_FLAGS = { '--token': 'token', '--agents': 'agents', '--cwd': 'cwd', '--permission-mode': 'permission_mode', '--command': 'command' };

function parseArgs(argv) {
  const opts = {
    server: SERVER,
    token: process.env.XIAOZHI_PRO_TOKEN || '',
    cwd: process.cwd(),
    permission_mode: 'native',
    command: '',
    agentArgs: [],
    agents: '',
    save: false,
    reset: false,
    help: false,
    version: false,
  };

  const unknown = [];
  let passthrough = false;

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    // `--` 之后的内容原样交给 Agent，这是唯一的透传通道
    if (passthrough) {
      opts.agentArgs.push(arg);
      continue;
    }
    if (arg === '--') {
      passthrough = true;
      continue;
    }
    if (BOOL_FLAGS[arg]) {
      opts[BOOL_FLAGS[arg]] = true;
      continue;
    }
    const [name, inline] = arg.startsWith('--') ? arg.slice(2).split('=', 2) : [];
    const key = `--${name}`;
    if (VALUE_FLAGS[key]) {
      const value = inline !== undefined ? inline : argv[++i];
      if (value === undefined) throw new Error(`${key} 缺少参数值`);
      opts[VALUE_FLAGS[key]] = value;
      continue;
    }
    // 未知参数直接报错，不再静默塞给 Agent：拼错参数会导致功能悄悄失效
    unknown.push(arg);
  }

  if (unknown.length) {
    throw new Error(
      `未知参数: ${unknown.join(' ')}\n可用参数见 --help。若要给 Agent 透传参数，写在 -- 之后，例如 xiaozhi-pro-connector -- --verbose`,
    );
  }
  // 单独写 --save 没有任何作用，直接指出来，别让用户以为选择被保存了
  if (opts.save && !opts.agents && !opts.help && !opts.version) {
    throw new Error('--save 需与 --agents 同用，例如 --agents claude,codex --save');
  }
  return opts;
}

function buildTargets(opts) {
  if (opts.command) {
    return [{
      agent_id: 'acp',
      name: AGENT_LABELS.acp,
      command: opts.command,
      agent_args: [...opts.agentArgs],
      agent_env: {},
    }];
  }
  return collectTargets(opts.permission_mode, opts.agentArgs);
}

async function main() {
  let opts;
  try {
    opts = parseArgs(process.argv.slice(2));
  } catch (err) {
    console.error(err.message);
    return 1;
  }
  if (opts.help) {
    console.log(HELP);
    return 0;
  }
  if (opts.version) {
    console.log(VERSION);
    return 0;
  }
  if (!opts.token) {
    console.error('缺少 --token 或环境变量 XIAOZHI_PRO_TOKEN');
    return 1;
  }

  const targets = buildTargets(opts);
  if (!targets.length) {
    const supported = AGENT_ORDER.filter((x) => x !== 'acp').map((x) => AGENT_LABELS[x]).join(' / ');
    console.error(`未检测到可用 Agent，请先安装：${supported}`);
    return 1;
  }

  if (targets.length === 1) {
    log(`自动探测到 ACP Agent: ${targets[0].name} (${targets[0].command})`);
  } else {
    log(`自动探测到 ${targets.length} 个 ACP Agent: ${targets.map((t) => t.name).join(', ')}`);
  }

  // 手动指定 --command 时是单 Agent 模式，不再走选择流程
  let chosen;
  try {
    chosen = opts.command ? targets : await selectTargets(targets, opts);
  } catch (err) {
    // 选择环节的失败都是可预期的（无终端、没选任何 Agent），只打印提示，不输出调用栈
    console.error(err.message);
    return 1;
  }
  if (!chosen.length) {
    console.error('没有可接入的 Agent，请检查 --agents 或配置文件中的 ID 是否已安装');
    return 1;
  }

  const connectors = chosen.map((target) => new Connector({ ...opts, ...target }));
  const stop = () => connectors.forEach((connector) => connector.stop());
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);

  await Promise.allSettled(connectors.map((connector) => connector.run()));
  return connectors.some((connector) => connector.fatal) ? 1 : 0;
}

main().then((code) => {
  process.exitCode = code;
}).catch((err) => {
  console.error(err.stack || err.message);
  process.exitCode = 1;
});
