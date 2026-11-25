# Xiaozhi Pro Agent Connector

![Node](https://img.shields.io/badge/node-%3E%3D20-brightgreen)
![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![Protocol](https://img.shields.io/badge/protocol-ACP-blue)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)

把本地 Agent 接入小智Pro的适配器。

支持 OpenClaw、Hermes、Codex、Claude Code、Qwen Code、OpenCode、Qoder、MiniMax Code、Kimi Code、CodeBuddy、DeepSeek Harness、Pi。

小智Pro 负责设备、语音前端、鉴权和回复投递；Connector 在用户本地自动探测已安装的 Agent，通过 ACP stdio 拉起，再桥接到小智Pro Agent Gateway。

## 为什么需要 Connector

- 小智Pro 维护一套 Agent WebSocket 协议，不再为 OpenClaw、Hermes 等分别写适配器。
- 用户本地装了什么 Agent，就自动接入什么 Agent。
- 一个 Connector 进程可以同时接入多个 Agent，每个 Agent 独立 `agent_id`、独立 ACP 子进程。

## 架构

```text
ESP32 / 小智设备
       |
       | 文本指令 / 回复取回
       v
小智Pro SaaS Agent Gateway
       |
       | WebSocket: /api/v1/agent/ws?agent_id=xxx
       v
XiaozhiPro Connector
       |
       | ACP JSON-RPC over stdio
       v
本地 Agent
OpenClaw / Hermes / Codex / Claude Code / Qwen Code / ...
```

## 特性

- 自动探测本地 Agent，无需手工配置启动命令
- 支持同时接入多个 Agent，互不覆盖连接
- ACP stdio ↔ WebSocket 双向桥接
- 支持 Node.js / npm 和 Python / uv 两种运行方式
- 跨平台：macOS / Linux / Windows
- 后台运行与进程停止命令齐全

## 快速开始

### 获取 API Key

前往：https://xzp.qlcloud.top/user-center/api

### Node.js / npm

```bash
npm install -g xiaozhi-pro-connector
export XIAOZHI_PRO_KEY=你的API密钥
xiaozhi-pro-connector
```

### Python / uv

```bash
cd python-connector
uv sync
export XIAOZHI_PRO_KEY=你的API密钥
uv run xiaozhi-pro-connector
```

## 后台运行

### Node.js / npm

Linux / macOS：

```bash
export XIAOZHI_PRO_KEY=你的API密钥

# 启动
nohup xiaozhi-pro-connector >> connector.log 2>&1 &

# 停止
pkill -f xiaozhi-pro-connector
```

Windows PowerShell：

```powershell
$env:XIAOZHI_PRO_KEY='你的API密钥'

# 启动
Start-Process xiaozhi-pro-connector -WindowStyle Hidden -RedirectStandardOutput connector.log -RedirectStandardError connector.err.log

# 停止
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'xiaozhi-pro-connector' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

### Python / uv

Linux / macOS：

```bash
export XIAOZHI_PRO_KEY=你的API密钥

# 启动
nohup uv run xiaozhi-pro-connector >> connector.log 2>&1 &

# 停止
pkill -f xiaozhi-pro-connector
```

Windows PowerShell：

```powershell
$env:XIAOZHI_PRO_KEY='你的API密钥'

# 启动
Start-Process uv -ArgumentList 'run','xiaozhi-pro-connector' -WindowStyle Hidden -RedirectStandardOutput connector.log -RedirectStandardError connector.err.log

# 停止
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'xiaozhi-pro-connector' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

## 支持的 Agent

| Agent | 接入方式 |
| --- | --- |
| OpenClaw | `openclaw acp` |
| Hermes | `hermes acp --accept-hooks` |
| Codex | `codex-acp` / `npx -y @agentclientprotocol/codex-acp@latest` |
| Claude Code | `claude-agent-acp` / `npx -y @agentclientprotocol/claude-agent-acp@latest` |
| Qwen Code | `qwen --acp` |
| OpenCode | `opencode acp` |
| Qoder | `qodercli --acp` |
| MiniMax Code | `mcode acp` |
| Kimi Code | `kimi acp` |
| CodeBuddy | `codebuddy --acp` |
| DeepSeek Harness | `dsh --profile acp` |
| Pi | `pi-acp` / `npx -y pi-acp@latest` |

## 文档

- Node 版安装、运行、配置：[node-connector/README.md](node-connector/README.md)
- Python 版安装、运行、配置：[python-connector/README.md](python-connector/README.md)

## 目录结构

```text
xiaozhi-pro-connector/
├── README.md
├── node-connector/
│   ├── README.md
│   ├── package.json
│   ├── bin/
│   │   └── xiaozhi-pro-connector.js
│   ├── src/
│   │   ├── acp.js
│   │   ├── cli.js
│   │   ├── connector.js
│   │   ├── detection.js
│   │   └── log.js
│   └── test/
│       ├── acp.test.js
│       ├── connector.test.js
│       └── detection.test.js
└── python-connector/
    ├── README.md
    ├── connector.py
    ├── pyproject.toml
    └── uv.lock
```

## 通信接口

Connector 连接：

```text
GET /api/v1/agent/ws?token=<API_KEY>&agent_id=<agent_id>
```

设备取回复：

```text
/agent_retrieve?agent_id=xxx
```

## License

Apache-2.0. See [LICENSE](LICENSE).
