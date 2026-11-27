# XiaozhiPro Connector (Node)

Node 版实现，发布为 npm 包 `@xiaozhi-pro/connector`。

安装、运行、API Key、自动探测和通信接口见 [../README.md](../README.md)。

## 常用命令

不装全局包，仓库里直接跑：

```bash
export XIAOZHI_PRO_TOKEN=你的API密钥

node bin/xiaozhi-pro-connector.js                       # 首次运行交互选择，结果写 ~/.xiaozhi-pro/connector.json
node bin/xiaozhi-pro-connector.js --reset               # 重新选择
node bin/xiaozhi-pro-connector.js --agents claude,codex --save   # 跳过交互并写配置
node bin/xiaozhi-pro-connector.js --version
node bin/xiaozhi-pro-connector.js --help

AGENT_DEBUG=1 node bin/xiaozhi-pro-connector.js         # Agent 起不来时看它的 stderr
npm test
```

Python 版对应 `uv run xiaozhi-pro-connector --reset`，参数完全一致。

## 目录结构

```text
node-connector/
├── package.json
├── README.md
├── publish.md
├── LICENSE
├── bin/
│   └── xiaozhi-pro-connector.js
├── src/
│   ├── cli.js
│   ├── config.js
│   ├── connector.js
│   ├── acp.js
│   ├── detection.js
│   ├── log.js
│   └── version.js
└── test/
    ├── acp.test.js
    ├── connector.test.js
    └── detection.test.js
```

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `package.json` | npm 包配置、依赖、`bin` 命令、发布文件白名单 |
| `publish.md` | npm 发布流程 |
| `bin/xiaozhi-pro-connector.js` | CLI 入口，调用 `src/cli.js` |
| `src/cli.js` | 参数解析、自动探测、启动多个 Connector |
| `src/config.js` | Agent 选择：配置文件读写、首次交互选择、`--agents` / `--reset` |
| `src/version.js` | 从 `package.json` 读版本号，唯一来源 |
| `src/detection.js` | 探测本地 Agent，组装启动命令、参数和环境变量 |
| `src/acp.js` | 本地 ACP 子进程：stdio 读写、JSON-RPC、session、权限 |
| `src/connector.js` | SaaS WebSocket 与本地 ACP 的桥接、消息队列、重连 |
| `src/log.js` | 统一日志输出 |
| `test/acp.test.js` | ACP 子进程测试：initialize / session / prompt |
| `test/connector.test.js` | WS 网关与 ACP 桥接测试 |
| `test/detection.test.js` | Agent 探测测试 |
| `LICENSE` | Apache-2.0 |

## npm 包文件

`package.json` 中 `files` 只发布：

```text
bin/
src/
README.md
LICENSE
```

`test/` 不进入 npm 包。
