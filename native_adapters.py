"""Official, locally authenticated CLI protocols. No credential handling."""
import asyncio
import contextlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import uuid
from pathlib import Path


API_ENV = {
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDECODE", "CLAUDE_CODE_OAUTH_TOKEN",
}

# Image tool results can exceed asyncio's usual line buffer. Bound Codex's
# JSONL messages separately; this is transport capacity, not model context.
CODEX_MESSAGE_LIMIT = 32 * 1024 * 1024


def subscription_env():
    """The user's environment without provider keys: for the user's own sign-in windows and status checks."""
    return {k: v for k, v in os.environ.items() if k.upper() not in API_ENV}


# What an agent's CLI may inherit (security bfc14da6): the system basics, proxy and certificate settings
# the CLIs need behind a proxy, and the CLI homes. Nothing else, so the user's own tokens and keys never
# reach an agent or its tool shells; API_ENV is still removed as a second check.
AGENT_ENV = {
    'PATH', 'PATHEXT', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'TMPDIR',
    'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'HOME', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA',
    'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE',
    'OS', 'USERNAME', 'USER', 'LOGNAME', 'SHELL', 'LANG', 'TERM',
    # macOS and Linux sessions: the keychain or secret service and the display for sign-in prompts.
    'XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'DBUS_SESSION_BUS_ADDRESS',
    'DISPLAY', 'WAYLAND_DISPLAY',
    'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'SSL_CERT_FILE', 'NODE_EXTRA_CA_CERTS', 'REQUESTS_CA_BUNDLE',
    'CODEX_HOME', 'CLAUDE_CONFIG_DIR',
}


def agent_env():
    """An agent CLI's environment: only AGENT_ENV names (any case) and LC_* locale settings."""
    return {k: v for k, v in os.environ.items()
            if (k.upper() in AGENT_ENV or k.upper().startswith('LC_')) and k.upper() not in API_ENV}


def command(provider):
    if provider == "antigravity":
        candidate = Path(__file__).resolve().parent / "providers/antigravity/agy.exe"
        if candidate.is_file():
            return [str(candidate)]
        executable = shutil.which("agy")
        if executable:
            return [executable]
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "agy/bin/agy.exe"
        if candidate.is_file():
            return [str(candidate)]
        raise RuntimeError("Official Antigravity CLI is not installed or discoverable")
    if provider == "gemini":
        node = shutil.which("node")
        bundles = [Path(__file__).resolve().parent / "providers/gemini/node_modules/@google/gemini-cli/bundle/gemini.js",
                   Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@google/gemini-cli/bundle/gemini.js"]
        for bundle in bundles:
            if node and bundle.is_file():
                return [node, str(bundle)]
    executable = shutil.which(provider)
    if executable and not (os.name == "nt" and Path(executable).suffix.lower() in (".cmd", ".bat")):
        return [executable]
    if os.name == "nt" and provider in ("codex", "claude"):
        candidate = Path.home() / (".codex/packages/standalone/current/bin/codex.exe" if provider == "codex"
                                   else ".local/bin/claude.exe")
        if candidate.exists():
            return [str(candidate)]
    raise RuntimeError(f"{provider} official CLI is not installed or discoverable")


async def spawn(run, args, env_overrides=None, *, stream_limit=2**22, room_env=False):
    """room_env: only Codex needs the room variables in its own process (it forwards them to the room MCP
    server through env_vars). Claude and Antigravity get them in their MCP configuration instead."""
    opts = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    child_env = agent_env()
    if room_env and hasattr(run, 'manager'):
        child_env.update(room_environment(run))
    child_env.update(env_overrides or {})
    proc = await asyncio.create_subprocess_exec(*args, cwd=str(run.worktree), env=child_env,
                                               stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                               stderr=asyncio.subprocess.PIPE, limit=stream_limit, **opts)
    run.process = proc
    async def drain():
        while data := await proc.stderr.read(4096):
            # Kept only in memory for diagnostics, never exposed as credential contents.
            run.stderr = (run.stderr + data.decode("utf-8", "replace"))[-4000:]
    run.stderr_task = asyncio.create_task(drain())
    return proc


def room_environment(run):
    # Child processes never receive the UI session token. The run's own capability token is
    # accepted only on its coordination route and image upload, and dies when the run ends.
    if not getattr(run, 'room_token', None):
        run.room_token = secrets.token_urlsafe(32)
    return {'AGENT_ROOM_URL': f"http://127.0.0.1:{run.manager.cfg['server']['port']}",
            'AGENT_ROOM_SESSION_TOKEN': run.room_token, 'AGENT_ROOM_RUN_ID': run.id}


def relay_setup(run, provider):
    """S5b: a run on an Anthropic or OpenAI API key connection uses the room relay (provider_relay.py)
    with a token minted for this run; it lives only on the run object and dies with the run. Returns
    (env, extra args), or ({}, []) for an agent on its subscription."""
    cfg = run.manager.cfg['agents'].get(run.name, {}) if hasattr(run, 'manager') else {}
    connection = cfg.get('key_target')
    if not connection or cfg.get('provider') != provider:
        return {}, []
    run.relay_token = secrets.token_urlsafe(32)
    base = f"http://127.0.0.1:{run.manager.cfg['server']['port']}/relay/{connection}"
    if provider == 'claude':
        # Command-line settings outrank the user's ~/.claude/settings.json, so its env can't move the
        # address. A key from the user's settings (env or apiKeyHelper) reaching the relay is refused.
        return {'ANTHROPIC_API_KEY': run.relay_token}, ['--settings', json.dumps({'env': {'ANTHROPIC_BASE_URL': base}})]
    provider_config = {'name': 'ZeroCollab relay', 'base_url': base + '/v1', 'env_key': 'ZEROCOLLAB_RELAY_TOKEN', 'wire_api': 'responses'}
    args = ['-c', 'model_provider="zerocollab_relay"']
    for key, value in provider_config.items():
        args += ['-c', f'model_providers.zerocollab_relay.{key}={json.dumps(value)}']
    # Tool shells don't see it: codex() turns on Codex's default KEY/SECRET/TOKEN excludes (security bfc14da6;
    # codex-cli 0.160 otherwise keeps them).
    return {'ZEROCOLLAB_RELAY_TOKEN': run.relay_token}, args


def local_setup(run):
    """A local model through Codex: Codex's own tools drive a model server on this computer, through the
    room relay (provider_relay: local servers drop Codex's MCP tool namespaces, so the relay offers the
    room tools as plain functions). Returns (env, -c arguments), or None for any other agent. Decided from
    the agent's connection, never from model output:
    - L1: automatic approvals are off for this run, so the thread is workspace-write with on-request
      approvals and every escalation goes to the user, whatever the room setting;
    - L2: the address is re-checked at launch, and by the relay on every request (loopback, own port, /v1);
    - L3: no user key: the only credential is this run's relay token, which dies with the run; tool
      shells drop KEY, SECRET and TOKEN variables, as for every Codex agent (codex());
    - L4: Codex's default workspace-write network policy (off) is kept; web search and connectors are off;
    - L5: the room MCP server and run token are the same as any agent's (codex() adds them)."""
    import provider_keys
    cfg = run.manager.cfg['agents'].get(run.name, {}) if hasattr(run, 'manager') else {}
    if cfg.get('provider') != 'codex' or not cfg.get('local') or not cfg.get('local_target'):
        return None
    provider_keys.local_codex_url(cfg.get('base_url', ''), provider_keys.room_ports(run.manager.cfg))
    run.automatic_tool_approvals = False
    run.relay_token = secrets.token_urlsafe(32)
    base = f"http://127.0.0.1:{run.manager.cfg['server']['port']}/relay/{cfg['local_target']}/v1"
    args = ['-c', 'model_provider="zerocollab_local"']
    for key, value in {'name': 'Local model server', 'base_url': base, 'wire_api': 'responses',
                       'env_key': 'ZEROCOLLAB_RELAY_TOKEN'}.items():
        args += ['-c', f'model_providers.zerocollab_local.{key}={json.dumps(value)}']
    args += ['-c', 'web_search="disabled"', '-c', 'features.apps=false']
    if os.name == 'nt':
        # Without a Windows sandbox choice Codex runs workspace-write threads read-only; unelevated needs no
        # administrator setup (the user's own home may use the elevated one).
        args += ['-c', 'windows.sandbox="unelevated"']
    if isinstance(cfg.get('context_tokens'), int):
        # Codex has no metadata for local model names; without this it assumes a default window.
        args += ['-c', f'model_context_window={cfg["context_tokens"]}']
    # Its own Codex home: no sign-in is needed, so none of the user's Codex configuration applies (their MCP
    # servers, connectors, global instructions or [shell_environment_policy.set] values). Threads persist here.
    home = Path(run.manager.data) / 'codex-local-home'
    home.mkdir(parents=True, exist_ok=True)
    return {'ZEROCOLLAB_RELAY_TOKEN': run.relay_token, 'CODEX_HOME': str(home)}, args


def room_server(run):
    return {'command': sys.executable, 'args': ['-X', 'utf8', str(Path(__file__).with_name('room_mcp.py'))],
            'env': room_environment(run)}


def install_antigravity_room(run):
    path = run.worktree / '.agents' / 'mcp_config.json'
    if not path.resolve().is_relative_to(run.worktree.resolve()):
        raise RuntimeError('Workspace MCP configuration resolves outside this agent worktree')
    original = path.read_bytes() if path.exists() else None
    config = json.loads(original) if original is not None else {}
    servers = config.setdefault('mcpServers', {})
    stale = [name for name, server in servers.items() if str(Path(__file__).with_name('room_mcp.py')) in server.get('args', [])]
    for name in stale:
        servers.pop(name)
    if stale:
        original = json.dumps(config, ensure_ascii=False).encode('utf-8')
    name = 'zerocollab_coordination'
    if name in servers:
        name += '_' + run.id
    servers[name] = room_server(run)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False), 'utf-8')
    run.room_mcp_backup = (path, original)


def restore_room_server(run):
    saved = getattr(run, 'room_mcp_backup', None)
    if saved:
        path, original = saved
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(original)
        run.room_mcp_backup = None


# Seconds an idle CLI gets to exit on stdin EOF before it is force-killed. Claude Code needs about
# half a second to shut down; killing it during an OAuth refresh loses the rotated refresh token
# and signs every Claude Code client on the machine out.
EXIT_GRACE = 10


async def _discard(stream):
    with contextlib.suppress(Exception):
        while await stream.read(65536):
            pass


async def terminate(run, grace=0):
    """Ends a run's CLI. grace > 0 first closes stdin so the CLI can exit on its own and finish
    pending writes such as rotated credentials; use it only when the CLI is idle or interrupted."""
    proc = run.process
    if proc and proc.returncode is None and grace:
        with contextlib.suppress(Exception):
            proc.stdin.close()
        # Keep stdout drained so a final write cannot block the exit on a full pipe.
        drain = asyncio.create_task(_discard(proc.stdout))
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), grace)
        drain.cancel()
    if proc and proc.returncode is None:
        if os.name == "nt":
            # Only a child that this run owns; never the shared model server.
            killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(killer.wait(), 5)
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), 3)
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    if run.stderr_task:
        run.stderr_task.cancel()


class Rpc:
    def __init__(self, run, proc, notify, incoming):
        self.run, self.proc, self.notify, self.incoming = run, proc, notify, incoming
        self.sequence = 0
        self.pending = {}
        self.handlers = set()
        self.read_error = None
        self.reader = asyncio.create_task(self.read())

    async def send(self, obj):
        self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        await self.proc.stdin.drain()

    async def request(self, method, params):
        if self.reader.done():
            raise self.read_error or RuntimeError("Official CLI disconnected")
        self.sequence += 1
        future = asyncio.get_running_loop().create_future()
        self.pending[self.sequence] = future
        await self.send({"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params})
        return await future

    async def respond(self, msg):
        try:
            result = await self.incoming(msg["method"], msg.get("params", {}))
            await self.send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32000, "message": str(exc)}})

    async def read(self):
        try:
            while line := await self.proc.stdout.readline():
                try:
                    msg = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if "method" in msg:
                    if "id" in msg:
                        task = asyncio.create_task(self.respond(msg))
                        self.handlers.add(task)
                        task.add_done_callback(self.handlers.discard)
                    else:
                        await self.notify(msg["method"], msg.get("params", {}))
                elif msg.get("id") in self.pending:
                    future = self.pending.pop(msg["id"])
                    if not future.done():
                        if "error" in msg:
                            future.set_exception(RuntimeError(str(msg["error"].get("message", msg["error"]))))
                        else:
                            future.set_result(msg.get("result", {}))
        except Exception as exc:
            self.read_error = RuntimeError(f"Official CLI protocol reader failed ({type(exc).__name__}): {exc}")
            self.read_error.__cause__ = exc
        finally:
            if self.read_error is None:
                self.read_error = RuntimeError("Official CLI disconnected")
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(self.read_error)
            self.pending.clear()

    async def close(self):
        self.reader.cancel()
        for task in self.handlers:
            task.cancel()
        await asyncio.gather(self.reader, *self.handlers, return_exceptions=True)


class ThreadBusy(RuntimeError):
    """Another Codex process (for example the user's Codex app) has this agent's saved thread open, so it
    cannot be resumed. Raised before the turn starts; the room runs the turn once on a new thread."""


def thread_busy(exc):
    return 'active writer' in str(exc)


class AuditMCPIsolation(ValueError):
    def __init__(self, names):
        super().__init__('Audit refused: non-room MCP servers require isolation')
        self.names = tuple(sorted(names))


async def verify_audit_codex(rpc, run, thread, *, audit_disabled=()):
    """Read only runner-reported permissions and tool names, never user config."""
    sandbox = thread.get('sandbox')
    safe = isinstance(sandbox, dict) and sandbox.get('type') == 'readOnly' and not sandbox.get('networkAccess', False)
    if not safe or thread.get('approvalPolicy') != 'on-request' or thread.get('approvalsReviewer') != 'user':
        raise ValueError('Audit Codex permissions were not reported as read-only/on-request/user')
    cursor, loaded, seen, extras = None, [], set(), set()
    for _ in range(20):
        page = await rpc.request('mcpServerStatus/list', {
            'threadId': thread['thread']['id'], 'limit': 100, 'cursor': cursor, 'detail': 'toolsAndAuthOnly'})
        data = page.get('data')
        if not isinstance(data, list):
            raise ValueError('Audit MCP inventory unavailable')
        for server in data:
            name = server.get('name')
            if not isinstance(name, str) or not name or len(name) > 128 or not name.isascii() or any(
                    not (c.isalnum() or c in '_-') for c in name):
                raise ValueError('Audit MCP inventory has an unsupported server identity')
            if name != 'zerocollab_coordination':
                if (name in audit_disabled and server.get('runtimeStatus') == 'disabled'
                        and server.get('tools') == {} and not server.get('toolsError')):
                    continue
                extras.add(name)
                if len(extras) > 32:
                    raise ValueError('Audit MCP isolation exceeded server bound')
                continue
            if server.get('toolsError'):
                raise ValueError('Audit room discovery failed')
            tools = server.get('tools')
            if not isinstance(tools, dict) or not tools:
                raise ValueError('Audit room tool inventory unavailable')
            loaded.extend('mcp__zerocollab_coordination__' + name for name in tools)
        cursor = page.get('nextCursor')
        if not cursor:
            break
        if cursor in seen:
            raise ValueError('Audit MCP inventory cursor repeated')
        seen.add(cursor)
    else:
        raise ValueError('Audit MCP inventory exceeded page bound')
    if not loaded:
        raise ValueError('Audit room MCP server was not loaded')
    if extras:
        raise AuditMCPIsolation(extras)
    await run.report_permissions({'approval_policy': thread['approvalPolicy'], 'sandbox': sandbox,
                                  'approval_reviewer': thread['approvalsReviewer'], 'loaded_tools': loaded,
                                  # Windows may report networkAccess=false without blocking network.
                                  'network_enforced': False if os.name == 'nt' else None})


async def codex(run, prompt, *, audit_disabled=()):
    audit = bool(getattr(run, 'manager_audit', None))
    if audit:
        run.automatic_tool_approvals = False
        run.previous_session = {}
    done = asyncio.get_running_loop().create_future()
    async def notify(method, params):
        if method == "item/agentMessage/delta":
            if await run.begin_message(params.get('itemId') or run.message_id or 'codex-message'):
                await run.delta(params.get("delta", ""))
        elif method in ("item/started", "item/completed"):
            item = params.get("item", {})
            if item.get('type') == 'agentMessage':
                if method == 'item/started':
                    await run.begin_message(item['id'], item.get('phase'))
                elif item.get('phase'):
                    await run.finish_message(item['id'], text=item.get('text'), phase=item['phase'])
                elif not run.text:
                    # Legacy app-server versions omit phase; keep the last message for turn completion.
                    if await run.begin_message(item['id']):
                        await run.delta(item.get('text', ''))
            elif item.get("type") not in ("userMessage", "reasoning"):
                await run.tool_event(item.get("type", "tool"), item, method.split("/")[-1])
        elif method == "turn/completed" and not done.done():
            turn = params.get("turn", {})
            if turn.get("status") == "failed":
                done.set_exception(RuntimeError(str(turn.get("error", "Codex turn failed"))))
            else:
                done.set_result(None)
        elif method == "error" and not params.get("willRetry") and not done.done():
            done.set_exception(RuntimeError(str(params.get("error", "Codex error"))))
        elif method == 'thread/tokenUsage/updated':
            usage = params.get('tokenUsage', {}).get('total', {})
            run.provider_usage_total = usage.get('totalTokens', 0)
            await run.report_usage({'total_tokens': max(0, run.provider_usage_total - run.previous_session.get('usage_total', 0)),
                                    'input_tokens': usage.get('inputTokens', 0),
                                    'output_tokens': usage.get('outputTokens', 0),
                                    'cached_input_tokens': usage.get('cachedInputTokens', 0)})

    async def incoming(method, params):
        if method.endswith("requestApproval"):
            allowed = await run.approval(method, params, "Official Codex permission request")
            return {"decision": "accept" if allowed else "decline"}
        if method == 'mcpServer/elicitation/request':
            # MCP tool approvals have a different wire schema from command approvals.
            # Auth and data elicitations remain interactive; no new account grants.
            if (params.get('_meta') or {}).get('codex_approval_kind') == 'mcp_tool_call':
                allowed = await run.approval(method, params, 'Official Codex MCP tool approval')
                return {'action': 'accept' if allowed else 'decline', 'content': {} if allowed else None}
            answer = await run.question(params)
            if not answer:
                return {'action': 'cancel', 'content': None}
            from jsonschema import validate
            content = json.loads(answer)
            validate(content, params.get('requestedSchema', {'type': 'object'}))
            return {'action': 'accept', 'content': content}
        if method == "item/tool/requestUserInput":
            answer = await run.question(params)
            return {"answers": {q["id"]: {"answers": [answer]} for q in params.get("questions", [])}}
        raise RuntimeError('Unsupported Codex client request: ' + method)

    server = room_server(run)
    args = command('codex') + ['app-server', '--stdio']
    local_args = local_setup(run)  # before the approval flags: a local model run never auto-approves
    if run.automatic_tool_approvals:
        args += ['-c', 'approval_policy="never"', '-c', 'sandbox_mode="danger-full-access"']
    # required: Codex otherwise gives optional MCP servers a 1s grace and starts the turn without the room
    # tools; required holds thread start until they are ready and fails the thread if they cannot start.
    # A cold Python start right after a deployment can be slow, so allow more than the 10s default.
    for key, value in {'command': server['command'], 'args': server['args'],
                       'env_vars': list(room_environment(run)), 'required': True,
                       'startup_timeout_sec': 30}.items():
        args += ['-c', 'mcp_servers.zerocollab_coordination.' + key + '=' + json.dumps(value)]
    relay_env, relay_args = local_args or relay_setup(run, 'codex')
    args += relay_args
    # Codex's process holds the room variables to forward them to the room server (env_vars above); its
    # tool shells drop every KEY, SECRET and TOKEN variable, room and relay tokens included (security bfc14da6).
    # Values the user's own config.toml sets in [shell_environment_policy.set] are still added for a
    # subscription agent, which needs that CODEX_HOME to sign in; a local-model agent has its own home.
    args += ['-c', 'shell_environment_policy.ignore_default_excludes=false']
    if audit:
        args += ['-c', 'approval_policy="on-request"', '-c', 'sandbox_mode="read-only"',
                 '-c', 'approvals_reviewer="user"', '-c', 'features.apps=false',
                 '-c', 'features.multi_agent=false', '-c', 'web_search="disabled"',
                 '-c', 'shell_environment_policy.ignore_default_excludes=false']
        for name in audit_disabled:
            args += ['-c', f'mcp_servers.{name}.enabled=false']
    proc = await spawn(run, args, relay_env, stream_limit=CODEX_MESSAGE_LIMIT, room_env=True)
    rpc = Rpc(run, proc, notify, incoming)
    try:
        await rpc.request("initialize", {"clientInfo": {"name": "zerocollab_local", "version": "0.1.0"},
                                         "capabilities": {"experimentalApi": True}})
        await rpc.send({"jsonrpc": "2.0", "method": "initialized"})
        thread_params = {"cwd": str(run.worktree),
                         "approvalPolicy": "never" if run.automatic_tool_approvals else "on-request",
                         "sandbox": "read-only" if audit else ("danger-full-access" if run.automatic_tool_approvals else "workspace-write"),
                         "ephemeral": False, "developerInstructions": run.instructions}
        if audit:
            thread_params['approvalsReviewer'] = 'user'
            thread_params['ephemeral'] = True
        model = run.settings['model'] or (run.manager.cfg['agents'][run.name].get('local_model') if local_args else '')
        if model:
            # A local connection's own model when the agent has none selected: Codex's default is an OpenAI model.
            thread_params['model'] = model
        if run.settings['effort'] and not local_args:
            thread_params['config'] = {'model_reasoning_effort': run.settings['effort']}
        method = 'thread/start'
        if run.previous_session:
            method = 'thread/resume'
            thread_params.pop('ephemeral')
            # Resume keeps model context; omit only the history in the RPC reply.
            thread_params.update(threadId=run.previous_session['id'], excludeTurns=True)
        try:
            thread = await rpc.request(method, thread_params)
        except RuntimeError as exc:
            if method == 'thread/resume' and thread_busy(exc):
                raise ThreadBusy(str(exc)) from exc
            raise
        if audit:
            try:
                await verify_audit_codex(rpc, run, thread, audit_disabled=audit_disabled)
            except AuditMCPIsolation as exc:
                if audit_disabled:
                    raise ValueError('Audit MCP isolation failed after one relaunch') from exc
                # No prompt was sent. Close this owned idle child before one isolated relaunch.
                # Discover names from the actual inventory; never read or edit user config.
                await rpc.close()
                await terminate(run, EXIT_GRACE)
                return await codex(run, prompt, audit_disabled=exc.names)
        else:
            await run.report_permissions({'approval_policy': thread.get('approvalPolicy'), 'sandbox': thread.get('sandbox')})
        await run.report_settings(model=thread.get('model'), effort=thread.get('reasoningEffort'))
        run.provider_session = thread["thread"]["id"]
        turn_params = {"threadId": run.provider_session, "input": [{"type": "text", "text": prompt}]}
        if run.settings['effort'] and not local_args:
            turn_params['effort'] = run.settings['effort']
        try:
            started = await rpc.request("turn/start", turn_params)
        except RuntimeError as exc:
            # The writer check can also come with the first turn on a resumed thread; nothing has run yet.
            if method == 'thread/resume' and thread_busy(exc):
                raise ThreadBusy(str(exc)) from exc
            raise
        active_turn = started['turn']['id']

        async def steer(text, request):
            if done.done() or rpc.reader.done():
                return False
            result = await rpc.request('turn/steer', {
                'threadId': run.provider_session, 'expectedTurnId': active_turn,
                'input': [{'type': 'text', 'text': text}],
            })
            if result.get('turnId') != active_turn:
                return False
            await run.steer_delivered(request)
            return True

        run.install_steer(steer)
        async def interrupt():
            if not done.done():
                await rpc.request('turn/interrupt', {'threadId': run.provider_session, 'turnId': active_turn})
        run.interrupt = interrupt
        # Reader disconnection must also release a wait for turn/completed.
        finished, _ = await asyncio.wait([done, rpc.reader], return_when=asyncio.FIRST_COMPLETED)
        if done not in finished:
            raise rpc.read_error or RuntimeError("Codex disconnected before completing its turn")
        await done
    finally:
        await rpc.close()


async def gemini(run, prompt):
    async def notify(method, params):
        if method != "session/update":
            return
        update = params.get("update", {})
        kind = update.get("sessionUpdate", "")
        if kind == "agent_message_chunk":
            content = update.get("content", {})
            if content.get("type") == "text":
                await run.delta(content.get("text", ""))
        elif kind in ("tool_call", "tool_call_update"):
            await run.tool_event(update.get("title", "Gemini tool"), update, update.get("status", "started"))

    async def incoming(method, params):
        if method == "session/request_permission":
            allowed = await run.approval("Gemini tool", params, "Official Gemini permission request")
            wanted = "allow_once" if allowed else "reject_once"
            option = next((o for o in params.get("options", []) if o.get("kind") == wanted), None)
            return {"outcome": {"outcome": "selected", "optionId": option["optionId"]}} if option else {"outcome": {"outcome": "cancelled"}}
        # ACP optional client filesystem/terminal capabilities are not advertised.
        raise RuntimeError("Client capability is disabled")

    proc = await spawn(run, command("gemini") + ["--acp", "--approval-mode", "default"])
    rpc = Rpc(run, proc, notify, incoming)
    try:
        await rpc.request("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                                         "clientInfo": {"name": "zerocollab", "version": "0.1.0"}})
        session = await rpc.request("session/new", {"cwd": str(run.worktree), "mcpServers": []})
        run.provider_session = session["sessionId"]
        result = await rpc.request("session/prompt", {"sessionId": run.provider_session,
                             "prompt": [{"type": "text", "text": run.instructions + "\n\n" + prompt}]})
        if result.get("stopReason") == "refusal" and not run.text:
            raise RuntimeError("Gemini refused this turn")
    finally:
        await rpc.close()


async def antigravity(run, prompt):
    # Official per-process mode; global settings and sign-in remain untouched.
    args = command("antigravity") + ["--input-format", "stream-json", "--output-format", "stream-json",
                                    "--print-timeout", str(run.limits['turn_max_seconds']) + 's']
    if run.automatic_tool_approvals:
        args.append('--dangerously-skip-permissions')
    install_antigravity_room(run)
    if run.previous_session:
        args += ['--conversation', run.previous_session['id']]
    selected_model = run.settings['model']
    option = next((m for m in run.manager.controls.catalogs[run.name]['models'] if m['value'] == selected_model), {})
    selected_model = option.get('effort_models', {}).get(run.settings['effort'], selected_model)
    if selected_model:
        args += ["--model", selected_model]
    if run.settings['effort']:
        args += ['--effort', run.settings['effort']]
    # room_env kept: the room server's env is also in .agents/mcp_config.json, but whether agy passes a
    # config env to its MCP servers instead of inheriting its own is unverified.
    proc = await spawn(run, args, room_env=True)
    proc.stdin.write((json.dumps({"event": "user", "message": {"content": run.instructions + "\n\n" + prompt}}) + "\n").encode())
    await proc.stdin.drain()
    proc.stdin.close()
    tool_errors = []
    while line := await proc.stdout.readline():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get('event') == 'init':
            run.provider_session = msg.get('conversation_id')
            await run.report_settings(model=msg.get('init', {}).get('model'))
            await run.report_permissions({'permission_mode': msg.get('init', {}).get('permission_mode')})
        elif msg.get("event") == "step_update":
            step = msg.get("step_update", {})
            if step.get("step_type") == "agent_response" and step.get("text_delta"):
                identifier = 'agy-' + str(step['step_index']) if 'step_index' in step else run.message_id or 'agy-response'
                if await run.begin_message(identifier):
                    await run.delta(step["text_delta"])
            if step.get("step_type") == "tool":
                error = step.get('tool_info', {}).get('error')
                if error:
                    tool_errors.append(error.get('message', str(error)) if isinstance(error, dict) else str(error))
                await run.tool_event(step.get("tool_name", "Antigravity tool"),
                    {**step.get("tool_info", {}), '_room_step': step.get('step_index')}, step.get("state", "started"))
        elif msg.get("event") == "result":
            result = msg.get("result", {})
            run.provider_session = result.get('conversation_id', run.provider_session)
            if result.get('usage'):
                await run.report_usage(result['usage'])
            if result.get("status") != "SUCCESS":
                raise RuntimeError(result.get("error") or "Antigravity turn did not complete")
            if not run.text and not run.published_messages and result.get("response"):
                await run.delta(result["response"])
            if not run.text.strip() and tool_errors:
                raise RuntimeError('Antigravity tool failure: ' + tool_errors[-1])
            return
    raise RuntimeError("Antigravity disconnected before completing its turn")


async def claude(run, prompt):
    audit = bool(getattr(run, 'manager_audit', None))
    if audit:
        run.automatic_tool_approvals = False
        run.previous_session = {}
    # --replay-user-messages echoes each stdin user message (isReplay) once the CLI adds it to the
    # conversation; mid-turn messages join the running turn at the next tool boundary.
    args = command("claude") + ["--print", "--verbose", "--input-format", "stream-json",
        "--output-format", "stream-json", "--include-partial-messages", "--replay-user-messages", "--permission-mode",
        "bypassPermissions" if run.automatic_tool_approvals else "manual",
        "--permission-prompt-tool", "stdio", "--mcp-config", json.dumps({'mcpServers': {'zerocollab_coordination': room_server(run)}}),
        "--append-system-prompt", run.instructions]
    if audit:
        args += ['--strict-mcp-config', '--tools', 'Read,Grep,Glob', '--disallowedTools',
                 'Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch,Task,Agent', '--no-session-persistence']
    if run.previous_session:
        args += ['--resume', run.previous_session['id']]
    if run.settings['model']:
        args += ['--model', run.settings['model']]
    if run.settings['effort']:
        args += ['--effort', run.settings['effort']]
    relay_env, relay_args = relay_setup(run, 'claude')
    args += relay_args
    env = {**relay_env, **({'CLAUDE_CODE_EFFORT_LEVEL': run.settings['effort']} if run.settings['effort'] else {})}
    proc = await spawn(run, args, env or None)
    async def send(obj):
        proc.stdin.write((json.dumps(obj) + "\n").encode())
        await proc.stdin.drain()
    await send({"type": "control_request", "request_id": "room-init", "request": {"subtype": "initialize", "hooks": {}}})
    sent = False
    audit_permissions = False
    message_id = None
    streamed_messages = set()
    # Sent steers by uuid -> original request. Delivery is the CLI's isReplay echo of that uuid
    # (queued commands replay with their source uuid); a result is never treated as delivery.
    steers = {}
    open_for_input = False
    usage_total = {}

    async def steer(text, request):
        """Runtime sender: True means written to the CLI, not accepted. Never waits for replay."""
        if not open_for_input:
            return False
        # Reusing a request's uuid is idempotent: the CLI skips a duplicate uuid and re-acknowledges it.
        identifier = request.setdefault('steer_uuid', str(uuid.uuid4()))
        steers[identifier] = request
        try:
            await send({"type": "user", "uuid": identifier, "session_id": "",
                        "message": {"role": "user", "content": text}, "parent_tool_use_id": None})
        except (OSError, RuntimeError):
            steers.pop(identifier, None)
            return False
        return True

    async def interrupt():
        """Runtime callback: official control interrupt; the runtime keeps process termination as fallback."""
        if not open_for_input:
            return False
        try:
            await send({"type": "control_request", "request_id": "room-interrupt-" + str(uuid.uuid4()),
                        "request": {"subtype": "interrupt"}})
        except (OSError, RuntimeError):
            return False
        return True

    async def next_line():
        if grace_deadline is None:
            return await proc.stdout.readline()
        # Steers sent before a result replay as a follow-on turn; absolute grace, not reset by other output.
        try:
            return await asyncio.wait_for(proc.stdout.readline(), max(0, grace_deadline - asyncio.get_running_loop().time()))
        except TimeoutError:
            return b''

    async def claude_event(msg):
        nonlocal sent, message_id, grace_deadline, open_for_input, audit_permissions
        kind = msg.get("type")
        if kind == 'system' and msg.get('subtype') == 'init':
            run.provider_session = msg.get('session_id')
            await run.report_settings(model=msg.get('model'), effort=msg.get('effort'))
            if audit:
                mode, tools = msg.get('permissionMode'), msg.get('tools')
                unexpected = [str(t)[:80] for t in tools if not isinstance(t, str) or (
                    t not in ('Read', 'Grep', 'Glob', 'EndConversation')
                    and not t.startswith('mcp__zerocollab_coordination__'))] if isinstance(tools, list) else None
                if mode != 'manual' or unexpected is None or unexpected:
                    # Say exactly what failed, so the check can be corrected without loosening it blind.
                    seen = 'no tool list' if unexpected is None else (
                        'unexpected tools: ' + ', '.join(unexpected[:12]) if unexpected else 'tools as expected')
                    raise ValueError(f'Audit Claude permissions/tool inventory not verified '
                                     f'(permission mode {str(mode)[:40]!r}; {seen})')
                audit_permissions = True
                await run.report_permissions({'permission_mode': mode, 'loaded_tools': tools})
            else:
                await run.report_permissions({'permission_mode': msg.get('permissionMode')})
        elif kind == "control_response" and not sent:
            response = msg.get('response', {})
            if response.get('request_id') != 'room-init':
                return False
            if response.get('subtype') == 'error':
                raise RuntimeError('Claude initialization failed')
            # Claude emits system/init after the first user message, unlike Codex's
            # pre-input thread response. Launch flags retain read-only tools; room
            # operations are checked again by the server's manager claim middleware.
            sent = True
            await send({"type": "user", "session_id": "", "message": {"role": "user", "content": prompt}, "parent_tool_use_id": None})
            open_for_input = True
            run.interrupt = interrupt
            install = getattr(run, 'install_steer', None)
            if install:
                install(steer)
        elif kind == "user" and msg.get('isReplay'):
            request = steers.pop(msg.get('uuid'), None)
            if request is not None:
                grace_deadline = None
                delivered = getattr(run, 'steer_delivered', None)
                if delivered:
                    await delivered(request)
        elif kind == "control_request":
            request = msg.get("request", {})
            allowed = False
            if request.get("subtype") == "can_use_tool":
                tool = request.get('tool_name', '')
                if audit:
                    allowed = audit_permissions and tool in {'Read', 'Grep', 'Glob'} | {
                        'mcp__zerocollab_coordination__' + name
                        for name in ('room_read', 'room_task', 'room_update', 'room_followup')}
                else:
                    allowed = await run.approval(tool or "Claude tool", request.get("input", {}), "Official Claude permission request")
            response = {"behavior": "allow", "updatedInput": request.get("input", {})} if allowed else {"behavior": "deny", "message": "Denied by user"}
            await send({"type": "control_response", "response": {"subtype": "success", "request_id": msg["request_id"], "response": response}})
        elif kind == "stream_event":
            event = msg.get("event", {})
            if event.get('type') == 'message_start':
                grace_deadline = None  # The follow-on turn is running; its own result re-arms the grace.
                message_id = event.get('message', {}).get('id') or str(msg.get('uuid'))
                await run.begin_message(message_id)
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                streamed_messages.add(message_id)
                await run.delta(delta.get("text", ""))
            if event.get('type') == 'message_delta':
                reason = delta.get('stop_reason')
                if reason:
                    run.message_phase = 'final_answer' if reason == 'end_turn' else 'commentary'
            if event.get('type') == 'message_stop':
                await run.finish_message(phase=run.message_phase or 'commentary')
            if event.get("type") == "content_block_start" and event.get("content_block", {}).get("type") == "tool_use":
                block = event["content_block"]
                await run.tool_event(block.get("name", "Claude tool"), block, "started")
        elif kind == "assistant":
            # Per-message fallback: never replay the full text after streamed deltas.
            content = msg.get('message', {})
            identifier = content.get('id') or msg.get('uuid') or message_id
            if identifier not in streamed_messages and identifier not in run.completed_messages:
                text = "\n\n".join(c.get("text", "") for c in content.get("content", []) if c.get("type") == "text")
                if text:
                    await run.begin_message(identifier)
                    await run.delta(text)
                    await run.finish_message(phase='final_answer' if content.get('stop_reason') == 'end_turn' else 'commentary')
        elif kind == "result":
            run.provider_session = msg.get('session_id', run.provider_session)
            if msg.get('usage'):
                # Each result covers its own turn; record_usage expects a run-cumulative total
                # (a follow-on steering turn adds a second result).
                for key, value in msg['usage'].items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        usage_total[key] = usage_total.get(key, 0) + value
                await run.report_usage(dict(usage_total))
            if msg.get("is_error"):
                raise RuntimeError(msg.get("result") or str(msg.get("errors", "Claude turn failed")))
            if not run.text and not run.published_messages and msg.get("result"):
                await run.delta(msg["result"])
            if not steers:
                open_for_input = False  # Synchronous with the check, so no steer slips in unread.
                return True
            # Unreplayed steers run as a follow-on turn; keep reading for at most 10s more.
            if grace_deadline is None:
                grace_deadline = asyncio.get_running_loop().time() + 10
        return False

    grace_deadline = None
    try:
        while line := await next_line():
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if await claude_event(msg):
                return
        if grace_deadline is not None:
            return  # Grace expired; unacknowledged steers stay queued in the runtime.
        raise RuntimeError("Claude disconnected before completing its turn")
    finally:
        open_for_input = False
        run.interrupt = None
