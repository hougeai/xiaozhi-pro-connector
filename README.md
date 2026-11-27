<div align="center">

# Xiaozhi Pro Agent Connector

**让小智开口就能指挥你电脑上的 Claude Code、Codex 干活。**

![Node](https://img.shields.io/badge/node-%3E%3D20-brightgreen)
![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![Protocol](https://img.shields.io/badge/protocol-ACP-blue)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)

[基本原理](#1-基本原理) · [快速开始](#2-快速开始) · [支持的 Agent](#3-支持的-agent) · [通信接口](#4-通信接口) · [目录结构](#5-目录结构)

</div>

[小智（xiaozhi-esp32）](https://github.com/78/xiaozhi-esp32)实现了设备端的实时语音对话，低延迟、即说即答。

[小智Pro](https://github.com/hougeai/xiaozhiPro) 在小智的基础上，给语音助手装上了 MCP 工具箱，语音即指令，开口即干活。

与此同时，本地 Agent（Claude Code、Codex 等）发展迅速，推理和执行能力强，却缺少一个语音入口。

如何把二者结合起来？

让设备端负责语音交互，本地 Agent 负责复杂任务，这就是本项目的出发点。

小智Pro 负责设备、语音前端、鉴权和回复投递；Connector 在用户本地自动探测已安装的 Agent，通过 ACP stdio 拉起，再桥接到小智Pro Agent Gateway。

## 1. 基本原理

### 1.1 架构

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

### 1.2 关键设计

| 特性 | 说明 |
| --- | --- |
| 自动探测 | 扫描本机已装的 Agent，无需手工配置启动命令 |
| 按需接入 | 首次运行选择要接入的 Agent，结果存入 `~/.xiaozhi-pro/connector.json`，未选中的不起进程 |
| 多 Agent | 一个 Connector 进程同时接入多个 Agent，每个 Agent 独立 ACP 子进程 |
| 多机分工 | 多台机器可分别接入不同 Agent；同一 Agent 只允许一台，第二台收到提示后退出，不会互踢 |
| 无需公网 | Connector 主动连接服务端，无需暴露端口 |
| 双向桥接 | ACP stdio ↔ WebSocket 双向转发 |
| 失败自愈 | 断线自动重连；密钥无效、Agent 已被占用等不可恢复错误直接提示并退出，不会无限重试 |
| 跨平台 | macOS / Linux / Windows，支持 Node.js / npm 和 Python / uv 两种运行方式 |

## 2. 快速开始

### 2.1 获取 API Key

前往：https://xzp.qlcloud.top/user-center/api

### 2.2 安装并启动

两种运行方式二选一，装哪种后面就用哪种启动。

**方式 A：Node.js / npm（需 Node >= 20）**

```bash
npm install -g @xiaozhi-pro/connector
export XIAOZHI_PRO_TOKEN=你的API密钥
xiaozhi-pro-connector
```

**方式 B：Python / uv（需 Python >= 3.10）**

```bash
git clone https://github.com/hougeai/xiaozhi-pro-connector.git
cd xiaozhi-pro-connector/python-connector
uv sync
export XIAOZHI_PRO_TOKEN=你的API密钥
uv run xiaozhi-pro-connector
```

### 2.3 选择要接入的 Agent

首次启动会列出探测到的 Agent，输入编号选择（回车表示全选），结果写入配置文件：

```text
检测到以下 Agent，选择要接入小智的（输入编号，多个用逗号分隔，直接回车全选）：

  1. Claude Code  (claude)
  2. Codex  (codex)
  3. Hermes  (hermes)

> 1,3
```

括号里是 Agent ID，也就是 `--agents` 参数要填的值。

选择结果写入配置文件，之后每次启动直接按配置接入，不再询问。

配置文件路径如下，可以直接编辑：

- Linux / macOS：`~/.xiaozhi-pro/connector.json`
- Windows：`C:\Users\<用户名>\.xiaozhi-pro\connector.json`

不想走交互，或者要改已保存的选择，可以用这几个参数：

```bash
xiaozhi-pro-connector --agents claude,codex        # 本次生效，不写配置
xiaozhi-pro-connector --agents claude,codex --save # 写入配置
xiaozhi-pro-connector --reset                      # 清空已保存的选择并重新选择
```

### 2.4 后台运行

首次选择必须在终端前台完成。后台运行要么已有配置文件，要么显式带 `--agents claude,codex --save`，否则启动会报错提示。

**方式 A：Node.js / npm**

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

**方式 B：Python / uv**

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

排查 Agent 起不来时，带 `AGENT_DEBUG=1` 启动，Agent 自身的报错会一起打进日志：

```bash
AGENT_DEBUG=1 xiaozhi-pro-connector
```

### 2.5 多台机器接入

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

## 3. 支持的 Agent

| Agent | ID（`--agents` 用） | 本地命令（自动探测，无需手动执行） |
| --- | --- | --- |
| OpenClaw | `openclaw` | `openclaw acp` |
| Hermes | `hermes` | `hermes acp --accept-hooks` |
| Codex | `codex` | `codex-acp` / `npx -y @agentclientprotocol/codex-acp@latest` |
| Claude Code | `claude` | `claude-agent-acp` / `npx -y @agentclientprotocol/claude-agent-acp@latest` |
| Qwen Code | `qwen` | `qwen --acp` |
| OpenCode | `opencode` | `opencode acp` |
| Qoder | `qoder` | `qodercli --acp` |
| MiniMax Code | `minimax` | `mcode acp` |
| Kimi Code | `kimi` | `kimi acp` |
| CodeBuddy | `codebuddy` | `codebuddy --acp` |
| DeepSeek Harness | `deepseek` | `dsh --profile acp` |
| Pi | `pi` | `pi-acp` / `npx -y pi-acp@latest` |

最后一列不用敲，Connector 认的是本机 PATH 里有没有这个可执行文件，缺适配器时会自己用 `npx` 拉一份。

注：桌面版 WorkBuddy 的底层就是 CodeBuddy，装上命令行版本一样能接进来。

## 4. 通信接口

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

## 5. 目录结构

```text
xiaozhi-pro-connector/
├── README.md
├── node-connector/
│   ├── README.md
│   ├── package.json
│   ├── bin/
│   │   └── xiaozhi-pro-connector.js   # CLI 入口
│   ├── src/
│   │   ├── cli.js            # 参数解析、探测、启动多个 Connector
│   │   ├── config.js         # Agent 选择：配置文件读写、交互选择
│   │   ├── connector.js      # WS 与 ACP 的桥接、重连、失败退出
│   │   ├── acp.js            # ACP 子进程：JSON-RPC、session、权限
│   │   ├── detection.js      # 探测本机 Agent，组装启动命令
│   │   ├── log.js            # 统一日志输出
│   │   └── version.js        # 版本号唯一来源（读 package.json）
│   └── test/
│       ├── acp.test.js
│       ├── connector.test.js
│       └── detection.test.js
└── python-connector/
    ├── README.md
    ├── connector.py          # 与 Node 版功能一致的单文件实现
    ├── pyproject.toml
    └── uv.lock
```

## 6. 更多文档

- Node 版安装、运行、配置：[node-connector/README.md](node-connector/README.md)
- Python 版安装、运行、配置：[python-connector/README.md](python-connector/README.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
