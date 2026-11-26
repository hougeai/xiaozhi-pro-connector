# Xiaozhi Pro Agent Connector

![Node](https://img.shields.io/badge/node-%3E%3D20-brightgreen)
![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![Protocol](https://img.shields.io/badge/protocol-ACP-blue)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)

[小智（xiaozhi-esp32）](https://github.com/78/xiaozhi-esp32)实现了设备端的实时语音对话，低延迟、即说即答。

[小智Pro](https://github.com/hougeai/xiaozhiPro) 在小智的基础上，给语音助手装上了 MCP 工具箱，语音即指令，开口即干活。

与此同时，本地 Agent（Claude Code、Codex 等）发展迅速，推理和执行能力强，却缺少一个语音入口。

如何把二者结合起来？让设备端负责语音交互，本地 Agent 负责复杂任务，这就是本项目的出发点。

本项目就是把本地 Agent 接入小智的适配器。

小智Pro 负责设备、语音前端、鉴权和回复投递；Connector 在用户本地自动探测已安装的 Agent，通过 ACP stdio 拉起，再桥接到小智Pro Agent Gateway。

目前已支持的主流 Agent 有：OpenClaw、Hermes、Codex、Claude Code、Qwen Code、OpenCode、Qoder、MiniMax Code、Kimi Code、CodeBuddy、DeepSeek Harness、Pi。

## 为什么需要 Connector

- 小智Pro 维护一套 Agent WebSocket 协议，不再为 OpenClaw、Hermes 等分别写适配器。
- 用户本地装了什么 Agent，就自动接入什么 Agent。
- 一个 Connector 进程可以同时接入多个 Agent，每个 Agent 独立 ACP 子进程。

## 架构

```text
ESP32 / 小智设备
       |
       | 文本指令 / 回复取回
       v
XiaozhiPro Agent Gateway
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
- 多台机器可分别接入不同 Agent；同一 Agent 同时只允许一台机器接入，第二台收到明确提示后退出，不会互踢
- ACP stdio ↔ WebSocket 双向桥接
- 支持 Node.js / npm 和 Python / uv 两种运行方式
- 跨平台：macOS / Linux / Windows
- 后台运行与进程停止命令齐全

## 快速开始

### 获取 API Key

前往：https://xzp.qlcloud.top/user-center/api

### Node.js / npm

```bash
npm install -g @xiaozhi-pro/connector
export XIAOZHI_PRO_TOKEN=你的API密钥
xiaozhi-pro-connector
```

### Python / uv

```bash
cd python-connector
uv sync
export XIAOZHI_PRO_TOKEN=你的API密钥
uv run xiaozhi-pro-connector
```

## 后台运行

### Node.js / npm

Linux / macOS：

```bash
export XIAOZHI_PRO_TOKEN=你的API密钥

# 启动
nohup xiaozhi-pro-connector >> connector.log 2>&1 &

# 停止
pkill -f xiaozhi-pro-connector
```

Windows PowerShell：

```powershell
$env:XIAOZHI_PRO_TOKEN='你的API密钥'

# 启动
Start-Process xiaozhi-pro-connector -WindowStyle Hidden -RedirectStandardOutput connector.log -RedirectStandardError connector.err.log

# 停止
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'xiaozhi-pro-connector' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

### Python / uv

Linux / macOS：

```bash
export XIAOZHI_PRO_TOKEN=你的API密钥

# 启动
nohup uv run xiaozhi-pro-connector >> connector.log 2>&1 &

# 停止
pkill -f xiaozhi-pro-connector
```

Windows PowerShell：

```powershell
$env:XIAOZHI_PRO_TOKEN='你的API密钥'

# 启动
Start-Process uv -ArgumentList 'run','xiaozhi-pro-connector' -WindowStyle Hidden -RedirectStandardOutput connector.log -RedirectStandardError connector.err.log

# 停止
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'xiaozhi-pro-connector' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

## 多台机器接入

同一 API 密钥可以在多台机器上分别运行 Connector，按 Agent 分工：

```bash
# 机器 A：装了 Claude Code、Codex
export XIAOZHI_PRO_TOKEN=你的API密钥
xiaozhi-pro-connector

# 机器 B：装了 OpenClaw、Hermes
export XIAOZHI_PRO_TOKEN=你的API密钥
xiaozhi-pro-connector
```

- 每台机器只接入本机探测到的 Agent，两台机器的连接互不影响，设备侧都能语音指挥
- 同一个 Agent 同时只允许一台机器接入；第二台会提示「已在其他机器接入」并退出，不会与第一台互踢
- 换机器前先停掉原机器上的 Connector；否则原连接要等 60 秒心跳超时失效，新机器才能接管

## 支持的 Agent

| Agent | 本地命令（自动探测，无需手动执行） |
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
│   ├── publish.md
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

设备发消息：

```text
POST /agent_send?agent_id=xxx
```

设备取回复：

```text
GET /agent_retrieve?agent_id=xxx
```

## License

Apache-2.0. See [LICENSE](LICENSE).
