"""XiaozhiPro Agent Connector.

把本地 ACP Agent 的 stdio JSON-RPC 桥接到小智Pro Agent Gateway 的 WebSocket。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import shutil
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

try:
    import websockets
except ImportError:
    sys.exit('缺少依赖 websockets，请先执行: uv sync')

PROTOCOL_VERSION = 1
try:  # 版本号唯一来源：包元数据（pyproject.toml）。取不到说明是未安装直接跑源码，不写死版本号
    from importlib.metadata import version as _pkg_version

    CONNECTOR_VERSION = _pkg_version('xiaozhi-pro-connector')
except Exception:
    CONNECTOR_VERSION = '0.0.0+unknown'
RECONNECT_DELAY = 5
STDERR_LIMIT = 12000
AGENT_DEBUG = os.getenv('AGENT_DEBUG') == '1'
SESSION_TIMEOUT = 60
SERVER_URL = 'wss://api.qlcloud.top/xzp'


def log(message: str) -> None:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{ts}] [connector] {message}', flush=True)


class ACPProcess:
    def __init__(self, command: str, args: list[str], cwd: str, permission_mode: str, env: Optional[dict[str, str]] = None):
        self.command = command
        self.args = args
        self.cwd = cwd
        self.permission_mode = permission_mode
        self.env = os.environ.copy()
        self.env.update(env or {})
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.pending: dict[int, asyncio.Future] = {}
        self.next_id = 1
        self.on_notification = None
        self.session_id: Optional[str] = None
        self.active_message_id: Optional[int] = None
        self.active_device_id = ''
        self.current_text: list[str] = []
        self.stderr_tail = ''

    async def start(self) -> None:
        if self.proc and self.proc.returncode is None:
            return
        log(f'启动 ACP Agent: {self.command} {" ".join(self.args)}')
        self.proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            cwd=self.cwd,
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        asyncio.create_task(self._read_stdout(), name='acp-stdout')
        asyncio.create_task(self._read_stderr(), name='acp-stderr')
        result = await self.request(
            'initialize',
            {
                'protocolVersion': PROTOCOL_VERSION,
                'clientCapabilities': {'elicitation': {'form': {}}},
                'clientInfo': {'name': 'xiaozhi-pro-connector', 'version': CONNECTOR_VERSION},
            },
            timeout=15,
        )
        if result.get('protocolVersion') != PROTOCOL_VERSION:
            raise RuntimeError(f"ACP 协议版本不兼容: {result.get('protocolVersion')}")
        # 每次新进程都要重建 session，否则重连后会沿用上一个已退出进程的 session_id
        self.session_id = None
        self.current_text = []
        self.active_message_id = None
        self.active_device_id = ''
        log('ACP initialize 完成')

    async def ensure_session(self) -> str:
        if not self.session_id:
            session = await self.request('session/new', {'cwd': self.cwd, 'mcpServers': []}, timeout=SESSION_TIMEOUT)
            self.session_id = session['sessionId']
            log(f'ACP session 创建完成: {self.session_id}')
        return self.session_id

    async def stop(self) -> None:
        if not self.proc:
            return
        if self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.proc.kill()
        self.proc = None
        for future in self.pending.values():
            if not future.done():
                future.cancel()
        self.pending.clear()

    async def _write(self, message: dict) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError('ACP Agent 未启动')
        line = json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.proc.stdin.write(line + b'\n')
        await self.proc.stdin.drain()

    async def request(self, method: str, params: dict, timeout: Optional[float] = None) -> dict:
        request_id = self.next_id
        self.next_id += 1
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        await self._write({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params})
        try:
            if timeout:
                return await asyncio.wait_for(future, timeout)
            return await future
        finally:
            self.pending.pop(request_id, None)

    async def notify(self, method: str, params: dict) -> None:
        await self._write({'jsonrpc': '2.0', 'method': method, 'params': params})

    async def respond(self, request_id: int, result: dict) -> None:
        await self._write({'jsonrpc': '2.0', 'id': request_id, 'result': result})

    async def _read_stdout(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        error: Optional[BaseException] = None
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                except Exception:
                    continue
                try:
                    await self._handle_message(message)
                except Exception as exc:
                    # 单条消息格式意外只记日志，不能让 stdout 读取循环挂掉
                    log(f'ACP 消息处理异常: {exc}')
                    continue
        except BaseException as exc:
            error = exc
            log(f'ACP stdout 读取失败: {exc}')
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError(f'ACP Agent 已退出: {error or self.stderr_tail[-500:]}'))
            self.pending.clear()

    async def _read_stderr(self) -> None:
        if not self.proc or not self.proc.stderr:
            return
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break
            text = line.decode('utf-8', errors='replace').strip()
            self.stderr_tail = (self.stderr_tail + text + '\n')[-STDERR_LIMIT:]
            if AGENT_DEBUG:
                log(f'agent: {text}')

    async def _handle_message(self, message: dict) -> None:
        request_id = message.get('id')
        method = message.get('method')
        if request_id is not None and 'result' in message:
            future = self.pending.get(request_id)
            if future and not future.done():
                future.set_result(message['result'])
            return
        if request_id is not None and 'error' in message:
            future = self.pending.get(request_id)
            if future and not future.done():
                error = message['error']
                future.set_exception(RuntimeError(f"{error.get('message', 'ACP 请求失败')}"))
            return
        if method == 'session/update':
            await self._handle_update(message.get('params', {}))
        elif method == 'session/request_permission':
            await self._handle_permission(request_id, message.get('params', {}))

    def _extract_text(self, update: dict) -> str:
        if update.get('sessionUpdate') not in ('agent_message_chunk', 'agent_thought_chunk'):
            return ''
        # ACP 协议中 content 是单个 ContentBlock，兼容个别 Agent 发数组的情况
        content = update.get('content')
        blocks = content if isinstance(content, list) else [content] if content else []
        parts = []
        for block in blocks:
            if isinstance(block, dict) and block.get('type') == 'text':
                parts.append(str(block.get('text', '')))
        text = ''.join(parts)
        if update.get('sessionUpdate') == 'agent_message_chunk':
            self.current_text.append(text)
        return text

    async def _handle_update(self, params: dict) -> None:
        update = params.get('update', {})
        # _extract_text 必须无条件调用，它负责把 agent_message_chunk 累积进 current_text，
        # 最终回复就靠它；只有「转发流式碎片」这一步是可选的
        text = self._extract_text(update)
        if text and self.on_notification:
            await self.on_notification(
                {
                    'type': 'update',
                    'text': text,
                    'role': 'agent',
                    'device_id': self.active_device_id,
                    'message_id': self.active_message_id,
                }
            )

    async def _handle_permission(self, request_id: Optional[int], params: dict) -> None:
        if request_id is None:
            return
        options = params.get('options', [])
        if self.permission_mode == 'full':
            for option in options:
                if option.get('kind') in ('allow_once', 'allow_always'):
                    log(f"自动允许权限: {option['optionId']}")
                    await self.respond(request_id, {'outcome': {'outcome': 'selected', 'optionId': option['optionId']}})
                    return
        log('拒绝权限请求')
        await self.respond(request_id, {'outcome': {'outcome': 'rejected'}})


class Connector:
    def __init__(self, args):
        self.args = args
        self.agent = ACPProcess(args.command, args.agent_args, args.cwd, args.permission_mode, getattr(args, 'agent_env', None))
        self.ws = None
        self.ws_lock = asyncio.Lock()
        self.prompt_queue: asyncio.Queue = asyncio.Queue()
        self.fatal = ''
        self.authed = False

    async def run(self) -> None:
        # 不挂 on_notification，即不向服务端转发 ACP 的 session/update 流式碎片。
        # 本场景只需要 Agent 的最终回复，服务端也会丢弃这些碎片（agent_gateway._message_loop 里 update 分支是 pass），
        # 一个长回复能产生数百条无效 WS 消息。将来要做控制台流式打字效果时，
        # 恢复这里的赋值，并在服务端把 update 写入对应 message_id 的记录即可。
        while not self.fatal:
            try:
                await self.agent.start()
                await self._connect()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.fatal:
                    log(f'接入失败: {self.fatal}')
                    break
                log(f'连接异常: {exc}')
            finally:
                await self.agent.stop()
                self.ws = None
            if self.fatal:
                break
            await asyncio.sleep(RECONNECT_DELAY)

    async def _connect(self) -> None:
        query = urlencode({'token': self.args.token, 'agent_id': self.args.agent_id})
        url = f'{self.args.server.rstrip("/")}/api/v1/agent/ws?{query}'
        log(f'连接 Agent Gateway: {self.args.server} agent_id={self.args.agent_id}')
        async with websockets.connect(url, max_size=None, open_timeout=10) as ws:
            self.ws = ws
            self.authed = False
            # 必须先读鉴权结果再发 register：服务端 accept 后立即回 auth_ok/auth_fail，
            # 密钥错误时它还紧接着关闭连接。此时先 send 会在已关闭的连接上抛 ConnectionClosed，
            # 把缓冲里的 auth_fail 丢掉，表现成「连接异常 + 无限重连」
            first = await asyncio.wait_for(ws.recv(), timeout=10)
            await self._handle_server_message(first if isinstance(first, str) else first.decode())
            # 成功日志由 auth_ok 触发，握手完成不代表鉴权通过
            # register：向服务端自报身份与能力。连接归属其实已由 URL 上的 token + agent_id 确定，
            # 服务端当前对 register 只 continue（见 agent_gateway._message_loop），
            # 保留它是为了协议完整性，以及将来按 capabilities 做能力协商。
            await self.send(
                {
                    'type': 'register',
                    'name': self.args.name,
                    'agent_id': self.args.agent_id,
                    'capabilities': {'delegation': False, 'permissions': True, 'nativeSessionHistory': False},
                }
            )
            worker = asyncio.create_task(self._prompt_worker(), name='prompt-worker')
            try:
                async for raw in ws:
                    await self._handle_server_message(raw)
            finally:
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
                log(f'连接断开: agent_id={self.args.agent_id}')
                while not self.prompt_queue.empty():
                    try:
                        self.prompt_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

    async def send(self, payload: dict) -> None:
        if not self.ws:
            # 回复与错误是有业务含义的消息，静默丢弃会让用户以为 Agent 没回，这里留一条日志
            if payload.get('type') in ('response', 'error'):
                log(f"{payload['type']} 未发送：连接不可用")
            return
        async with self.ws_lock:
            await self.ws.send(json.dumps(payload, ensure_ascii=False, separators=(',', ':')))

    async def _handle_server_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except Exception:
            return
        msg_type = data.get('type')
        if msg_type == 'auth_ok':
            if not self.authed:
                self.authed = True
                log(f'连接成功: agent_id={self.args.agent_id}')
            return
        if msg_type == 'auth_fail':
            # 鉴权类失败（密钥无效、配额不足、该 Agent 已在其他机器接入）都不可能靠重试解决，
            # 一律标记致命并退出该 Agent 的循环，避免无限重连和反复拉起子进程
            self.fatal = data.get('message') or f"鉴权失败（{data.get('error')}）"
            raise RuntimeError(self.fatal)
        if msg_type == 'ping':
            await self.send({'type': 'pong'})
        elif msg_type == 'message':
            await self.prompt_queue.put(data)
        elif msg_type == 'cancel':
            if self.agent.session_id:
                await self.agent.notify('session/cancel', {'sessionId': self.agent.session_id})
        else:
            log(f'忽略服务端消息: {msg_type}')

    async def _prompt_worker(self) -> None:
        while True:
            message = await self.prompt_queue.get()
            try:
                await self._handle_prompt(message)
            except Exception as exc:
                log(f'任务失败: {exc}')
                await self.send(
                    {
                        'type': 'error',
                        'text': str(exc),
                        'role': 'error',
                        'device_id': message.get('device_id', ''),
                        'message_id': message.get('message_id'),
                    }
                )
            finally:
                self.prompt_queue.task_done()

    async def _handle_prompt(self, message: dict) -> None:
        text = str(message.get('text', ''))
        if not text:
            return
        session_id = await self.agent.ensure_session()
        message_id = message.get('message_id')
        device_id = str(message.get('device_id', ''))
        self.agent.active_message_id = message_id
        self.agent.active_device_id = device_id
        self.agent.current_text = []
        # 换行换成空格再截，否则日志会被拆成多行难以阅读
        ask = re.sub(r'[\r\n]+', ' ', text)
        log(
            f"收到任务: agent_id={self.args.agent_id} message_id={message_id} device_id={device_id or '-'} "
            f"text={ask[:10]}{'...' if len(ask) > 10 else ''}"
        )
        result = await self.agent.request(
            'session/prompt',
            {'sessionId': session_id, 'prompt': [{'type': 'text', 'text': text}]},
        )
        if not ''.join(self.agent.current_text).strip():
            content = result.get('content') or []
            for block in content if isinstance(content, list) else [content]:
                if isinstance(block, dict) and block.get('type') == 'text':
                    self.agent.current_text.append(str(block.get('text', '')))
        stop_reason = result.get('stopReason', 'end_turn')
        if stop_reason != 'end_turn':
            log(f'任务未完成: agent_id={self.args.agent_id} message_id={message_id} stopReason={stop_reason}')
            await self.send(
                {
                    'type': 'error',
                    'text': f'Agent 未完成: {stop_reason}',
                    'role': 'error',
                    'device_id': device_id,
                    'message_id': message_id,
                }
            )
            return
        final_text = ''.join(self.agent.current_text).strip()
        answer = re.sub(r'[\r\n]+', ' ', final_text)
        log(f'回复完成: agent_id={self.args.agent_id} message_id={message_id} response={answer[:10]}{"..." if len(answer) > 10 else ""}')
        await self.send(
            {'type': 'response', 'text': final_text, 'role': 'agent', 'device_id': device_id, 'message_id': message_id}
        )


def _is_executable(path: str) -> bool:
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def _find_executable(command: str, env_names: str | tuple[str, ...] = ()) -> str:
    if isinstance(env_names, str):
        env_names = (env_names,) if env_names else ()
    for env_name in env_names:
        path = os.getenv(env_name, '').strip()
        if _is_executable(path):
            return path

    candidates: list[str] = []
    nvm_dir = Path(os.getenv('NVM_DIR') or (Path.home() / '.nvm')) / 'versions' / 'node'
    for root in (
        nvm_dir,
        Path.home() / '.npm-global/bin',
        Path.home() / '.bun/bin',
        Path('/opt/homebrew/bin'),
        Path('/usr/local/bin'),
        Path.home() / '.local/bin',
    ):
        if root.name == 'node':
            candidates.extend(str(p) for p in sorted(root.glob(f'*/bin/{command}'), reverse=True))
        else:
            candidates.append(str(root / command))
    if command == 'openclaw':
        candidates.insert(0, str(Path.home() / '.openclaw-bundle/wrapper/openclaw'))

    for path in dict.fromkeys(candidates):
        if _is_executable(path):
            return path
    return shutil.which(command) or ''


def _local_agent(command: str, env_names: tuple[str, ...], base_args: list[str], permission_mode: str, full_args: list[str] = []) -> tuple[str, list[str], dict[str, str]]:
    path = _find_executable(command, env_names)
    if not path:
        return '', [], {}
    args = list(base_args)
    if permission_mode == 'full' and full_args:
        args.extend(full_args)
    return path, args, {}


def _adapter_agent(
    parent_command: str,
    parent_env: tuple[str, ...],
    adapter_command: str,
    adapter_env: tuple[str, ...],
    package: str,
    parent_env_keys: tuple[str, ...] = (),
) -> tuple[str, list[str], dict[str, str]]:
    parent = _find_executable(parent_command, parent_env)
    env = {key: parent for key in parent_env_keys} if parent else {}
    adapter = _find_executable(adapter_command, adapter_env)
    if adapter:
        return adapter, [], env
    npx = _find_executable('npx', ('NPX_BIN',))
    if parent and npx:
        return npx, ['-y', package], env
    return '', [], {}


def _openclaw_agent() -> tuple[str, list[str], dict[str, str]]:
    path = _find_executable('openclaw', ('OPENCLAW_ACP_BIN', 'OPENCLAW_BIN', 'OPENCLAW_PATH'))
    if not path:
        return '', [], {}
    args = ['acp', '--verbose']
    raw_url = (os.getenv('OPENCLAW_GATEWAY_URL') or os.getenv('OPENCLAW_WS_URL') or os.getenv('OPENCLAW_BASE_URL', '')).strip()
    if raw_url:
        if raw_url.startswith('https://'):
            raw_url = 'wss://' + raw_url[len('https://'):]
        elif raw_url.startswith('http://'):
            raw_url = 'ws://' + raw_url[len('http://'):]
        args += ['--url', raw_url]
    token = (os.getenv('OPENCLAW_GATEWAY_TOKEN') or os.getenv('OPENCLAW_TOKEN', '')).strip()
    if token:
        args += ['--token', token]
    return path, args, {}


def _build_agent(key: str, permission_mode: str) -> tuple[str, list[str], dict[str, str]]:
    if key == 'openclaw':
        return _openclaw_agent()
    if key == 'hermes':
        return _local_agent('hermes', ('HERMES_BIN',), ['acp', '--accept-hooks'], permission_mode)
    if key == 'qwen':
        return _local_agent('qwen', ('QWEN_CODE_BIN', 'QWEN_BIN'), ['--acp'], permission_mode)
    if key == 'opencode':
        return _local_agent('opencode', ('OPENCODE_BIN', 'OPENCODE_PATH'), ['acp'], permission_mode)
    if key == 'qoder':
        return _local_agent('qodercli', ('QODERCLI_PATH', 'QODER_CLI_PATH'), ['--acp'], permission_mode, ['--dangerously-skip-permissions'])
    if key == 'minimax':
        return _local_agent('mcode', ('MINIMAX_CODE_BIN', 'MINIMAX_BIN'), ['acp'], permission_mode)
    if key == 'kimi':
        return _local_agent('kimi', ('KIMI_CODE_BIN', 'KIMI_BIN'), ['acp'], permission_mode)
    if key == 'codebuddy':
        return _local_agent('codebuddy', ('CODEBUDDY_BIN', 'CODEBUDDY_PATH'), ['--acp'], permission_mode, ['--dangerously-skip-permissions'])
    if key == 'deepseek':
        return _local_agent('dsh', ('DEEPSEEK_HARNESS_BIN', 'DSH_BIN'), ['--profile', 'acp'], permission_mode)
    if key == 'codex':
        return _adapter_agent('codex', ('CODEX_PATH', 'CODEX_BIN'), 'codex-acp', ('CODEX_ACP_BIN', 'CODEX_ACP_PATH'), '@agentclientprotocol/codex-acp@latest', ('CODEX_PATH',))
    if key == 'claude':
        return _adapter_agent('claude', ('CLAUDE_CODE_EXECUTABLE', 'CLAUDE_BIN'), 'claude-agent-acp', ('CLAUDE_CODE_ACP_BIN', 'CLAUDE_AGENT_ACP_BIN'), '@agentclientprotocol/claude-agent-acp@latest', ('CLAUDE_CODE_EXECUTABLE',))
    if key == 'pi':
        return _adapter_agent('pi', ('PI_BIN', 'PI_COMMAND'), 'pi-acp', ('PI_ACP_BIN',), 'pi-acp@latest', ('PI_COMMAND', 'PI_BIN'))
    if key == 'acp':
        acp_command = os.getenv('ACP_COMMAND', '').strip()
        if acp_command:
            return acp_command, shlex.split(os.getenv('ACP_ARGS', '')), {}
    return '', [], {}


AGENT_ORDER = ['openclaw', 'hermes', 'codex', 'qwen', 'opencode', 'qoder', 'minimax', 'kimi', 'codebuddy', 'claude', 'deepseek', 'pi', 'acp']
AGENT_LABELS = {
    'acp': 'ACP',
    'claude': 'Claude Code',
    'codebuddy': 'CodeBuddy',
    'codex': 'Codex',
    'deepseek': 'DeepSeek',
    'hermes': 'Hermes',
    'kimi': 'Kimi Code',
    'minimax': 'MiniMax Code',
    'opencode': 'OpenCode',
    'openclaw': 'OpenClaw',
    'pi': 'Pi',
    'qoder': 'Qoder',
    'qwen': 'Qwen Code',
}


def _make_target(key: str, command: str, agent_args: list[str], env: dict[str, str], extra_args: list[str]) -> dict:
    return {
        'agent_id': key,
        'name': AGENT_LABELS.get(key, key),
        'command': command,
        'agent_args': list(agent_args) + list(extra_args),
        'agent_env': env,
    }


def collect_targets(permission_mode: str = 'native', extra_args: Optional[list[str]] = None) -> list[dict]:
    extra = list(extra_args or [])
    targets = []
    for key in AGENT_ORDER:
        command, args, env = _build_agent(key, permission_mode)
        if command:
            targets.append(_make_target(key, command, args, env, extra))
    return targets


# 配置文件统一放用户 home 下的 .xiaozhi-pro，三个平台路径规则一致：
#   Linux   /home/<用户名>/.xiaozhi-pro/connector.json
#   macOS   /Users/<用户名>/.xiaozhi-pro/connector.json
#   Windows C:\Users\<用户名>\.xiaozhi-pro\connector.json
CONFIG_DIR = Path.home() / '.xiaozhi-pro'
CONFIG_PATH = CONFIG_DIR / 'connector.json'


def load_config() -> Optional[list[str]]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        return None
    agents = data.get('agents')
    return agents if isinstance(agents, list) else None


def save_config(agents: list[str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({'agents': agents}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    log(f'已保存 Agent 选择到 {CONFIG_PATH}')


def prompt_targets(targets: list[dict]) -> list[str]:
    print('\n检测到以下 Agent，选择要接入小智的（输入编号，多个用逗号分隔，直接回车全选）：\n')
    # 括号里给 agent id，与 --agents 的取值一致；启动路径在探测日志里已经打过，这里不重复
    for index, target in enumerate(targets, 1):
        print(f'  {index}. {target["name"]}  ({target["agent_id"]})')
    answer = input('\n> ').strip()
    if not answer:
        return [target['agent_id'] for target in targets]
    picked = []
    # 提示只教逗号，解析顺带吃全角逗号与空格，避免输入法差异被判成无效输入
    for raw in re.split(r'[,，\s]+', answer.strip()):
        if raw.isdigit() and 1 <= int(raw) <= len(targets):
            picked.append(targets[int(raw) - 1]['agent_id'])
    return list(dict.fromkeys(picked))


def announce(chosen: list[dict], source: str = '') -> None:
    # 空选择由调用方报错退出，这里不再打一条「接入 0 个」的噪音日志
    if not chosen:
        return
    prefix = f'{source}，' if source else ''
    log(f'{prefix}本次接入 {len(chosen)} 个 Agent: ' + ', '.join(target['name'] for target in chosen))


def filter_by_ids(targets: list[dict], ids: list[str], source: str) -> list[dict]:
    known = {target['agent_id'] for target in targets}
    unknown = [agent_id for agent_id in ids if agent_id not in known]
    if unknown:
        log(f'{source}中的 {", ".join(unknown)} 本机未检测到，已跳过')
    return [target for target in targets if target['agent_id'] in ids]


def select_targets(targets: list[dict], agents: str, save: bool, reset: bool) -> list[dict]:
    """决定本次接入哪些 Agent，优先级 --agents > 配置文件 > 交互式选择（无终端报错）。

    --reset 清空已保存的选择并重新交互选择，新结果覆盖写回配置。
    """
    if agents:
        chosen = filter_by_ids(targets, [x.strip() for x in re.split(r'[,，]', agents) if x.strip()], '--agents')
        if save:
            save_config([target['agent_id'] for target in chosen])
        announce(chosen, '按 --agents')
        return chosen

    reset_hint = False
    if reset:
        reset_hint = CONFIG_PATH.exists()
    else:
        configured = load_config()
        if configured:
            chosen = filter_by_ids(targets, configured, str(CONFIG_PATH))
            if chosen:
                announce(chosen, f'按 {CONFIG_PATH}')
                return chosen
            log(f'{CONFIG_PATH} 里记录的 Agent 本机都没检测到，重新选择')

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        reason = (
            f'{CONFIG_PATH} 已存在，但当前不是交互终端，无法交互选择'
            if CONFIG_PATH.exists()
            else f'未找到 {CONFIG_PATH}，且当前不是交互终端（nohup / CI 场景）'
        )
        raise SystemExit(
            f'{reason}。\n'
            '请先在终端前台运行一次 xiaozhi-pro-connector 完成选择，或用 --agents claude,codex 指定。'
        )

    if reset_hint:
        log('已清空之前的选择')
    ids = prompt_targets(targets)
    if not ids:
        raise SystemExit('没有选择任何 Agent，启动结束')
    save_config(ids)
    chosen = [target for target in targets if target['agent_id'] in ids]
    announce(chosen)
    return chosen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='小智Pro Agent Connector')
    parser.add_argument('--token', default=os.getenv('XIAOZHI_PRO_TOKEN', ''), help='小智Pro API Key')
    parser.add_argument('--agents', default='', help='本次只接入指定 Agent，逗号分隔，如 claude,codex')
    parser.add_argument('--save', action='store_true', help='与 --agents 同用，把选择写入配置文件')
    parser.add_argument('--reset', action='store_true', help='清空已保存的 Agent 选择并重新选择')
    parser.add_argument('--command', default='', help=argparse.SUPPRESS)
    parser.add_argument('--cwd', default=os.getcwd(), help=argparse.SUPPRESS)
    parser.add_argument('--permission-mode', choices=['native', 'full'], default='native', help=argparse.SUPPRESS)
    args, extra = parser.parse_known_args()
    # argparse 不再定义位置参数：`--` 之后的内容才是给 Agent 的透传，其余未识别参数一律报错
    if extra and extra[0] == '--':
        args.agent_args = extra[1:]
    elif extra:
        parser.error(
            f"未知参数: {' '.join(extra)}。可用参数见 --help；"
            '若要给 Agent 透传参数，写在 -- 之后，例如 xiaozhi-pro-connector -- --verbose'
        )
    else:
        args.agent_args = []
    args.server = SERVER_URL
    if not args.token:
        parser.error('缺少 --token 或环境变量 XIAOZHI_PRO_TOKEN')
    # 单独写 --save 没有任何作用，直接指出来，别让用户以为选择被保存了
    if args.save and not args.agents:
        parser.error('--save 需与 --agents 同用，例如 --agents claude,codex --save')

    if args.command:
        args.targets = [{
            'agent_id': 'acp',
            'name': AGENT_LABELS['acp'],
            'command': args.command,
            'agent_args': list(args.agent_args),
            'agent_env': {},
        }]
    else:
        args.targets = collect_targets(args.permission_mode, args.agent_args)
        if not args.targets:
            supported = ' / '.join(AGENT_LABELS[x] for x in AGENT_ORDER if x != 'acp')
            parser.error(f'未检测到可用 Agent，请先安装：{supported}')
        # 先报探测结果，再按配置/交互筛选，否则这句日志里的数字会变成选中数
        if len(args.targets) == 1:
            first = args.targets[0]
            log(f'自动探测到 ACP Agent: {first["name"]} ({first["command"]})')
        else:
            log(f'自动探测到 {len(args.targets)} 个 ACP Agent: {", ".join(t["name"] for t in args.targets)}')
        args.targets = select_targets(args.targets, args.agents, args.save, args.reset)
        if not args.targets:
            parser.error('没有可接入的 Agent，请检查 --agents 或配置文件中的 ID 是否已安装')
        return args

    first = args.targets[0]
    log(f'自动探测到 ACP Agent: {first["name"]} ({first["command"]})')
    return args


async def main() -> int:
    args = parse_args()
    tasks = []
    connectors = []
    for target in args.targets:
        # 显式合并：command / agent_args 在 args 和 target 里都有，直接双星号传参会报
        # “got multiple values for keyword argument”，这里让 target 覆盖 args
        merged = vars(args).copy()
        merged.update(target)
        one = argparse.Namespace(**merged)
        connector = Connector(one)
        connectors.append(connector)
        tasks.append(asyncio.create_task(connector.run(), name=f'connector-{target["agent_id"]}'))
    loop = asyncio.get_running_loop()

    def stop() -> None:
        for task in tasks:
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop)
        except NotImplementedError:
            pass
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        log('收到停止信号，已退出')
    finally:
        for task in tasks:
            task.cancel()
    # 鉴权类失败视为致命，进程返回非零，便于 nohup / 脚本判断
    return 1 if any(connector.fatal for connector in connectors) else 0


def cli() -> None:
    sys.exit(asyncio.run(main()))


if __name__ == '__main__':
    cli()
