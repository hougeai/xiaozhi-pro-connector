# XiaozhiPro Connector (Node)

Node 版实现，发布为 npm 包 `@xiaozhi-pro/connector`。

安装、运行、API Key、自动探测和通信接口见 [../README.md](../README.md)。

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
│   ├── connector.js
│   ├── acp.js
│   ├── detection.js
│   └── log.js
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
