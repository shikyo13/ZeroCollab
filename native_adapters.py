"""Official, locally authenticated CLI protocols. No credential handling."""
import asyncio
import contextlib
import json
import os
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
    return {k: v for k, v in os.environ.items() if k.upper() not in API_ENV}


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


async def spawn(run, args, env_overrides=None, *, stream_limit=2**22):
    opts = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    child_env = subscription_env()
    if hasattr(run, 'manager'):
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
    return {'AGENT_ROOM_URL': f"http://127.0.0.1:{run.manager.cfg['server']['port']}",
            'AGENT_ROOM_SESSION_TOKEN': run.manager.host.session_token, 'AGENT_ROOM_RUN_ID': run.id}


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


async def codex(run, prompt):
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
    if run.automatic_tool_approvals:
        args += ['-c', 'approval_policy="never"', '-c', 'sandbox_mode="danger-full-access"']
    # required: Codex otherwise gives optional MCP servers a 1s grace and starts the turn without the room
    # tools; required holds thread start until they are ready and fails the thread if they cannot start.
    # A cold Python start right after a deployment can be slow, so allow more than the 10s default.
    for key, value in {'command': server['command'], 'args': server['args'],
                       'env_vars': list(room_environment(run)), 'required': True,
                       'startup_timeout_sec': 30}.items():
        args += ['-c', 'mcp_servers.zerocollab_coordination.' + key + '=' + json.dumps(value)]
    proc = await spawn(run, args, stream_limit=CODEX_MESSAGE_LIMIT)
    rpc = Rpc(run, proc, notify, incoming)
    try:
        await rpc.request("initialize", {"clientInfo": {"name": "zerocollab_local", "version": "0.1.0"},
                                         "capabilities": {"experimentalApi": True}})
        await rpc.send({"jsonrpc": "2.0", "method": "initialized"})
        thread_params = {"cwd": str(run.worktree),
                         "approvalPolicy": "never" if run.automatic_tool_approvals else "on-request",
                         "sandbox": "danger-full-access" if run.automatic_tool_approvals else "workspace-write",
                         "ephemeral": False, "developerInstructions": run.instructions}
        if run.settings['model']:
            thread_params['model'] = run.settings['model']
        if run.settings['effort']:
            thread_params['config'] = {'model_reasoning_effort': run.settings['effort']}
        method = 'thread/start'
        if run.previous_session:
            method = 'thread/resume'
            thread_params.pop('ephemeral')
            # Resume keeps model context; omit only the history in the RPC reply.
            thread_params.update(threadId=run.previous_session['id'], excludeTurns=True)
        thread = await rpc.request(method, thread_params)
        await run.report_permissions({'approval_policy': thread.get('approvalPolicy'), 'sandbox': thread.get('sandbox')})
        await run.report_settings(model=thread.get('model'), effort=thread.get('reasoningEffort'))
        run.provider_session = thread["thread"]["id"]
        turn_params = {"threadId": run.provider_session, "input": [{"type": "text", "text": prompt}]}
        if run.settings['effort']:
            turn_params['effort'] = run.settings['effort']
        started = await rpc.request("turn/start", turn_params)
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
    proc = await spawn(run, args)
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
    # --replay-user-messages echoes each stdin user message (isReplay) once the CLI adds it to the
    # conversation; mid-turn messages join the running turn at the next tool boundary.
    args = command("claude") + ["--print", "--verbose", "--input-format", "stream-json",
        "--output-format", "stream-json", "--include-partial-messages", "--replay-user-messages", "--permission-mode",
        "bypassPermissions" if run.automatic_tool_approvals else "manual",
        "--permission-prompt-tool", "stdio", "--mcp-config", json.dumps({'mcpServers': {'zerocollab_coordination': room_server(run)}}),
        "--append-system-prompt", run.instructions]
    if run.previous_session:
        args += ['--resume', run.previous_session['id']]
    if run.settings['model']:
        args += ['--model', run.settings['model']]
    if run.settings['effort']:
        args += ['--effort', run.settings['effort']]
    proc = await spawn(run, args, {'CLAUDE_CODE_EFFORT_LEVEL': run.settings['effort']} if run.settings['effort'] else None)
    async def send(obj):
        proc.stdin.write((json.dumps(obj) + "\n").encode())
        await proc.stdin.drain()
    await send({"type": "control_request", "request_id": "room-init", "request": {"subtype": "initialize", "hooks": {}}})
    sent = False
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
        nonlocal sent, message_id, grace_deadline, open_for_input
        kind = msg.get("type")
        if kind == 'system' and msg.get('subtype') == 'init':
            run.provider_session = msg.get('session_id')
            await run.report_settings(model=msg.get('model'), effort=msg.get('effort'))
            await run.report_permissions({'permission_mode': msg.get('permissionMode')})
        elif kind == "control_response" and not sent:
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
                allowed = await run.approval(request.get("tool_name", "Claude tool"), request.get("input", {}), "Official Claude permission request")
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
