"""XiaozhiPro Agent Connector.

把本地 ACP Agent 的 stdio JSON-RPC 桥接到小智Pro Agent Gateway 的 WebSocket。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
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
RECONNECT_DELAY = 5
STDERR_LIMIT = 12000
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
                'clientInfo': {'name': 'xiaozhi-pro-connector', 'version': '1.0.0'},
            },
            timeout=15,
        )
        if result.get('protocolVersion') != PROTOCOL_VERSION:
            raise RuntimeError(f"ACP 协议版本不兼容: {result.get('protocolVersion')}")
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
            self.stderr_tail = (self.stderr_tail + line.decode('utf-8', errors='replace').strip() + '\n')[-STDERR_LIMIT:]
            log(f'agent: {line.decode("utf-8", errors="replace").strip()}')

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
        if not self.on_notification:
            return
        update = params.get('update', {})
        text = self._extract_text(update)
        if text:
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

    async def run(self) -> None:
        self.agent.on_notification = self.send
        while True:
            try:
                await self.agent.start()
                await self._connect()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f'连接异常: {exc}')
            finally:
                await self.agent.stop()
                self.ws = None
            await asyncio.sleep(RECONNECT_DELAY)

    async def _connect(self) -> None:
        query = urlencode({'token': self.args.token, 'agent_id': self.args.agent_id})
        url = f'{self.args.server.rstrip("/")}/api/v1/agent/ws?{query}'
        log(f'连接 Agent Gateway: {self.args.server} agent_id={self.args.agent_id}')
        async with websockets.connect(url, max_size=None, open_timeout=10) as ws:
            self.ws = ws
            log(f'连接成功: agent_id={self.args.agent_id}')
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
            return
        if msg_type == 'auth_fail':
            raise RuntimeError(f"鉴权失败: {data.get('message')}")
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
        log(f"收到任务: agent_id={self.args.agent_id} message_id={message_id} device_id={device_id or '-'}")
        result = await self.agent.request(
            'session/prompt',
            {'sessionId': session_id, 'prompt': [{'type': 'text', 'text': text}]},
        )
        if not ''.join(self.agent.current_text).strip():
            for block in result.get('content', []):
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
        preview = ' '.join(final_text[:10].split())
        log(f'回复完成: agent_id={self.args.agent_id} message_id={message_id} response={preview}...')
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
    for root in (
        Path.home() / '.nvm/versions/node',
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='小智Pro Agent Connector')
    parser.add_argument('--token', default=os.getenv('XIAOZHI_PRO_TOKEN', ''), help='小智Pro API Key')
    parser.add_argument('--command', default='', help=argparse.SUPPRESS)
    parser.add_argument('--cwd', default=os.getcwd(), help=argparse.SUPPRESS)
    parser.add_argument('--permission-mode', choices=['native', 'full'], default='native', help=argparse.SUPPRESS)
    parser.add_argument('agent_args', nargs=argparse.REMAINDER, help='传给 Agent 的参数')
    args = parser.parse_args()
    args.server = SERVER_URL
    if args.agent_args and args.agent_args[0] == '--':
        args.agent_args = args.agent_args[1:]
    if not args.token:
        parser.error('缺少 --token 或环境变量 XIAOZHI_PRO_TOKEN')

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

    if len(args.targets) == 1:
        first = args.targets[0]
        log(f'自动探测到 ACP Agent: {first["name"]} ({first["command"]})')
    else:
        log(f'自动探测到 {len(args.targets)} 个 ACP Agent: {", ".join(t["name"] for t in args.targets)}')
    return args


async def main() -> None:
    args = parse_args()
    tasks = []
    for target in args.targets:
        one = argparse.Namespace(**vars(args), **target)
        tasks.append(asyncio.create_task(Connector(one).run(), name=f'connector-{target["agent_id"]}'))
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


def cli() -> None:
    asyncio.run(main())


if __name__ == '__main__':
    cli()
