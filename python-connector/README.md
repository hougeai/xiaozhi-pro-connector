# XiaozhiPro Connector (Python)

Python 版实现。

安装、运行、API Key、自动探测和通信接口见 [../README.md](../README.md)。

## 目录结构

```text
python-connector/
├── connector.py
├── pyproject.toml
├── uv.lock
└── README.md
```

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `connector.py` | Python 版全部实现：Agent 探测、ACP 子进程、WS 桥接、CLI |
| `pyproject.toml` | Python 包配置、依赖、`xiaozhi-pro-connector` 命令入口 |
| `uv.lock` | uv 依赖锁定 |
| `README.md` | Python 版模块说明 |

## `connector.py` 内部职责

| 部分 | 作用 |
| --- | --- |
| `AGENT_ORDER` / `AGENT_LABELS` | 支持的 Agent 列表和展示名 |
| `_find_executable` / `_build_agent` | 探测可执行文件并组装启动参数 |
| `collect_targets` | 一次性收集本地所有可用 Agent |
| `ACPProcess` | ACP stdio 子进程、JSON-RPC、session、权限 |
| `Connector` | SaaS WebSocket 与 ACP 子进程桥接 |
| `parse_args` / `main` | CLI 参数、启动多个 Agent、信号退出 |

## 命令入口

`pyproject.toml` 中定义：

```toml
[project.scripts]
xiaozhi-pro-connector = "connector:cli"
```
