import { AGENT_LABELS, AGENT_ORDER, collectTargets } from './detection.js';
import { Connector } from './connector.js';
import { log } from './log.js';

const VERSION = '1.0.0';
const SERVER = 'wss://api.qlcloud.top/xzp';

const HELP = `Usage: xiaozhi-pro-connector [options]

Options:
  --token <token>          小智Pro API Key，也可用 XIAOZHI_PRO_KEY
  -h, --help               显示帮助
  -v, --version            显示版本
`;

function parseArgs(argv) {
  const opts = {
    server: SERVER,
    token: process.env.XIAOZHI_PRO_KEY || '',
    cwd: process.cwd(),
    permission_mode: 'native',
    command: '',
    agentArgs: [],
    help: false,
    version: false,
  };

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--') {
      opts.agentArgs.push(...argv.slice(i + 1));
      break;
    }
    if (arg === '-h' || arg === '--help') {
      opts.help = true;
      continue;
    }
    if (arg === '-v' || arg === '--version') {
      opts.version = true;
      continue;
    }
    if (arg.startsWith('--')) {
      let [key, value] = arg.slice(2).split('=', 2);
      if (value === undefined) {
        value = argv[++i];
      }
      switch (key) {
        case 'token': opts.token = value; break;
        case 'cwd': opts.cwd = value; break;
        case 'permission-mode': opts.permission_mode = value; break;
        case 'command': opts.command = value; break;
        default:
          if (value !== undefined) i--;
          opts.agentArgs.push(arg);
          if (value !== undefined) opts.agentArgs.push(value);
      }
      continue;
    }
    opts.agentArgs.push(arg);
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
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help) {
    console.log(HELP);
    return 0;
  }
  if (opts.version) {
    console.log(VERSION);
    return 0;
  }
  if (!opts.token) {
    console.error('缺少 --token 或环境变量 XIAOZHI_PRO_KEY');
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

  const connectors = targets.map((target) => new Connector({ ...opts, ...target }));
  const stop = () => connectors.forEach((connector) => connector.stop());
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);

  await Promise.allSettled(connectors.map((connector) => connector.run()));
  return 0;
}

main().then((code) => {
  process.exitCode = code;
}).catch((err) => {
  console.error(err.stack || err.message);
  process.exitCode = 1;
});
