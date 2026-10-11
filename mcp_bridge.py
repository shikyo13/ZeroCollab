"""MCP server for agent chat tools — runs alongside the web server.

Serves two transports for compatibility:
  - streamable-http on port 8200 (Claude Code, Codex, Qwen)
  - SSE on port 8201 (Gemini)
"""

import json
import time
import logging
import threading
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

from durable import atomic_write

log = logging.getLogger(__name__)

# Shared state — set by run.py before starting
store = None
room_settings = None  # set by run.py — dict with "channels" list etc.
registry = None       # set by run.py — RuntimeRegistry instance
config = None         # set by run.py — full config.toml dict
router = None         # set by run.py — Router instance
agents = None         # set by run.py — AgentManager instance
deployment = None
channel_guard = None  # set by room_runtime.install: callable(channel) -> True if the active project owns it
_presence: dict[str, float] = {}
_activity: dict[str, bool] = {}   # True = screen changed on last poll
_activity_ts: dict[str, float] = {}  # timestamp of last active=True heartbeat
ACTIVITY_TIMEOUT = 8  # auto-expire activity after 8s without a fresh active=True
_presence_lock = threading.Lock()   # guards both _presence and _activity
_renamed_from: set[str] = set()    # old names from renames — suppress leave messages
_cursors: dict[str, dict[str, int]] = {}  # agent_name → {channel_name → last_id}
_cursors_lock = threading.Lock()
_cursor_persistence_lock = threading.Lock()
_empty_read_count: dict[str, int] = {}  # sender → consecutive empty reads
# Last channel each agent explicitly read from. chat_send falls back to
# this when the caller omits the channel, so agents mentioned in #X don't
# accidentally reply in #general just because they forgot the channel
# param. Closes #58.
_last_read_channel: dict[str, str] = {}
_last_read_lock = threading.Lock()
PRESENCE_TIMEOUT = 10  # ~2 missed heartbeats (5s interval) = offline

# Cursor persistence — set by run.py to enable saving cursors across restarts
_CURSORS_FILE: Path | None = None

_MCP_INSTRUCTIONS = (
    "ZeroCollab - a shared chat channel for coordinating development between AI agents and humans. "
    "Use chat_send to post messages. Use chat_read to check recent messages. "
    "Use chat_join when you start a session to announce your presence. "
    "Always use your own name as the sender — never impersonate other agents or humans.\n\n"
    "CRITICAL — Sender Identity Rules:\n"
    "Your BASE agent identity (used for chat_claim and chat_read) is:\n"
    "  - All Anthropic products (Claude Code, claude-cli, etc.) → base: \"claude\"\n"
    "  - All OpenAI products (Codex CLI, codex, chatgpt-cli, etc.) → base: \"codex\"\n"
    "  - All Google products (Gemini CLI, gemini-cli, aistudio, etc.) → base: \"gemini\"\n"
    "  - All Alibaba/Qwen products (Qwen Code, qwen-cli, etc.) → base: \"qwen\"\n"
    "  - All Kilo products (Kilo CLI, kilocode, etc.) → base: \"kilo\"\n"
    "  - Humans use their own name (e.g. \"user\")\n"
    "Do NOT use your CLI tool name (e.g. \"gemini-cli\", \"claude-code\") — use the base name above.\n"
    "IMPORTANT: When multiple instances run, the server renames slot 1 (e.g. \"claude\" → \"claude-1\"). "
    "If chat_send rejects your sender, call chat_claim(sender='your_base_name') and use the confirmed_name "
    "as your sender for ALL subsequent tool calls. The confirmed_name overrides the base name.\n\n"
    "CRITICAL — Identity:\n"
    "Always use your base agent name (claude/codex/gemini/qwen/kilo) as sender. "
    "Do NOT call chat_claim on fresh sessions — it is only for "
    "recovering a previous identity after /resume.\n\n"
    "CRITICAL — Always Respond In Chat:\n"
    "When you are addressed in a chat message (@yourname or @all agents), you MUST respond using chat_send "
    "in the same channel. NEVER respond only in your terminal/console output. The human and other agents "
    "cannot see your terminal — only chat messages are visible to everyone. If you need to do work first, "
    "do the work, then post your response/results in chat using chat_send.\n\n"
    "CRITICAL — Token-Aware Reading:\n"
    "Each chat_read call costs tokens. Default: one read per relevant channel per turn. "
    "A second read is fine if you can name the reason (checked a different channel, did work and expect a reply, "
    "recovering from an error). After an empty read ('No new messages'), do NOT read the same channel again — "
    "stop and wait for your next prompt. Never use chat_read as a sleep/wait loop.\n\n"
    "Messages belong to channels (default: 'general'). Use the 'channel' parameter in chat_send and "
    "chat_read to target a specific channel. Omit channel or pass empty string to read from all channels.\n\n"
    "If you are addressed in chat, respond in chat — use chat_send to reply in the same channel. "
    "Do not take the answer back to your terminal session. "
    "If the latest message in a channel is addressed to you (or all agents), treat it as your active task "
    "and execute it directly. Reading a channel with no task addressed to you is just catching up — no action needed.\n\n"
    "Multi-instance support:\n"
    "When multiple instances of the same agent run simultaneously, each gets a unique identity.\n"
    "The server assigns names like claude-1, claude-2 automatically.\n"
    "On /resume, if your conversation history shows you previously used a different name (e.g. 'claude-music'), "
    "call chat_claim(sender='your_base_name', name='claude-music') to reclaim it.\n"
    "If chat_send rejects your sender with an identity error, call chat_claim first to get your identity."
)

# --- Tool implementations (shared between both servers) ---


def _request_headers(ctx: Context | None):
    if ctx is None:
        return None
    try:
        request = ctx.request_context.request
    except Exception:
        return None
    return getattr(request, "headers", None)


def _extract_agent_token(ctx: Context | None) -> str:
    headers = _request_headers(ctx)
    if not headers:
        return ""
    auth = headers.get("authorization", "")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return headers.get("x-agent-token", "").strip()


def _authenticated_instance(ctx: Context | None) -> dict | None:
    if not registry:
        return None
    token = _extract_agent_token(ctx)
    if not token:
        return None
    return registry.resolve_token(token)


def _resolve_tool_identity(
    raw_name: str,
    ctx: Context | None,
    *,
    field_name: str,
    required: bool = False,
    acting: bool = False,
) -> tuple[str, str | None]:
    """The caller's name. acting=True (posting, joining, claiming) requires an authenticated agent
    session: an unauthenticated MCP client could otherwise post as 'user' or any non-agent name, and
    the room would treat it as the human (wakes, @all claims). People post from the room UI."""
    provided = raw_name.strip() if raw_name else ""
    token = _extract_agent_token(ctx)
    inst = _authenticated_instance(ctx)
    if inst:
        resolved = inst["name"]
        if resolved:
            _touch_presence(resolved)
        return resolved, None
    if token:
        return "", "Error: stale or unknown authenticated agent session. Re-register and retry."
    if acting:
        return "", ("Error: this needs an authenticated agent session. Agents act through their own session; "
                    "people post from the room UI.")

    if not provided:
        if required:
            return "", f"Error: {field_name} is required."
        return "", None

    if registry:
        resolved = registry.resolve_name(provided)
        if resolved != provided and registry.is_registered(resolved):
            provided = resolved
        if registry.is_agent_family(provided):
            return "", f"Error: authenticated agent session required for '{provided}'."

    # An unauthenticated reader's name is only a read cursor: it must not mark anyone as online.
    return provided, None


def chat_send(
    sender: str,
    message: str,
    image_path: str = "",
    reply_to: int = -1,
    channel: str = "",
    ctx: Context | None = None,
) -> str:
    """Send a message to the ZeroCollab chat as your authenticated agent session (sender is taken
    from the session; launchers inject it). Unauthenticated connections cannot post.
    Optionally attach a local image by providing image_path (absolute path).
    Optionally reply to a message by providing reply_to (message ID).
    Channel resolution:
      - If you pass channel explicitly, that target is honored.
      - If you omit it, the message is routed to the last channel this
        sender read from via chat_read (so replying after
        `chat_read(channel="bugfixing")` lands in #bugfixing, not #general).
      - If this sender has never read anything, the message falls back to
        the 'general' channel."""
    sender, err = _resolve_tool_identity(sender, ctx, field_name="sender", required=True, acting=True)
    if err:
        return err

    # Fallback routing: if caller omitted the channel, use whatever channel
    # this sender last read from. Prevents agents that forget the channel
    # param from accidentally replying in #general.
    if sender and not channel:
        with _last_read_lock:
            channel = _last_read_channel.get(sender, "")
    # Final fallback if still nothing: original 'general' behavior.
    if not channel:
        channel = "general"
    # Block pending instances (identity not yet confirmed)
    if registry and registry.is_pending(sender):
        return "Error: identity not confirmed. Call chat_claim(sender=your_base_name) to get your identity."
    # Block base family names when multi-instance is active
    # (but allow if sender is a registered+active instance — e.g. slot-1 'claude' that already claimed)
    if registry and sender in registry.get_bases() and registry.family_instance_count(sender) >= 2:
        inst = registry.get_instance(sender)
        if not inst or inst.get("state") != "active":
            return (f"Error: multiple {sender} instances are registered. "
                    f"Call chat_claim(sender='{sender}') to get your unique identity, then use the confirmed_name as sender.")
    # Block unregistered agent names (stale identity from resumed session)
    if registry and registry.is_agent_family(sender) and not registry.is_registered(sender):
        return f"Error: sender '{sender}' is not registered. Call chat_claim(sender=your_base_name) to get your identity."
    if not message.strip() and not image_path:
        return "Empty message, not sent."
    if channel_guard and not channel_guard(channel):
        return f"Error: #{channel} is not a chat in the current project (it belongs to another project or is read-only history)."

    attachments = []
    if image_path:
        import shutil
        import uuid
        from pathlib import Path
        src = Path(image_path)
        if not src.exists():
            return f"Image not found: {image_path}"
        if src.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'):
            return f"Unsupported image type: {src.suffix}"
        
        # Get upload dir from config (fall back to ./uploads)
        raw_dir = "./uploads"
        if config and "images" in config:
            raw_dir = config["images"].get("upload_dir", raw_dir)
        upload_dir = Path(raw_dir)
        
        upload_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:8]}{src.suffix}"
        shutil.copy2(str(src), str(upload_dir / filename))
        attachments.append({"name": src.name, "url": f"/uploads/{filename}"})

    reply_id = reply_to if reply_to >= 0 else None
    if reply_id is not None and store.get_by_id(reply_id) is None:
        return f"Message #{reply_to} not found."

    msg = store.add(sender, message.strip(), attachments=attachments,
                    reply_to=reply_id, channel=channel)
    _update_cursor(sender, [msg], channel)
    with _presence_lock:
        _presence[sender] = time.time()
    return f"Sent (id={msg['id']})"


def _resolve_attachments(attachments: list[dict]) -> list[dict]:
    """Add absolute file_path to attachments so agents can read images."""
    if not attachments:
        return attachments
    raw_dir = "./uploads"
    if config and "images" in config:
        raw_dir = config["images"].get("upload_dir", raw_dir)
    upload_dir = Path(raw_dir).resolve()
    resolved = []
    for att in attachments:
        a = dict(att)
        url = a.get("url", "")
        if url.startswith("/uploads/"):
            filename = url.split("/")[-1]
            a["file_path"] = str(upload_dir / filename)
        resolved.append(a)
    return resolved


def _serialize_messages(msgs: list[dict]) -> str:
    """Serialize store messages into MCP chat_read output shape."""
    out = []
    for m in msgs:
        entry = {
            "id": m["id"],
            "sender": m["sender"],
            "text": m["text"],
            "type": m["type"],
            "time": m["time"],
            "channel": m.get("channel", "general"),
        }
        if m.get("attachments"):
            entry["attachments"] = _resolve_attachments(m["attachments"])
        if m.get("reply_to") is not None:
            entry["reply_to"] = m["reply_to"]
        out.append(entry)
    return json.dumps(out, ensure_ascii=False) if out else ""


def _load_cursors():
    """Load cursor state from disk (called by run.py after store init)."""
    global _cursors
    if _CURSORS_FILE is None or not _CURSORS_FILE.exists():
        return
    try:
        data = json.loads(_CURSORS_FILE.read_text("utf-8"))
        with _cursors_lock:
            _cursors.update(data)
    except Exception:
        log.warning("Failed to load cursor state from %s", _CURSORS_FILE)


def _save_cursors():
    """Persist cursor state to disk atomically (write temp + rename)."""
    if _CURSORS_FILE is None:
        return
    try:
        with _cursor_persistence_lock:
            with _cursors_lock:
                content = json.dumps(_cursors)
            atomic_write(_CURSORS_FILE, lambda stream: stream.write(content))
    except Exception:
        log.warning("Failed to save cursor state to %s", _CURSORS_FILE)


def migrate_identity(old_name: str, new_name: str):
    """Migrate all runtime state when an agent is renamed (presence, cursors, activity)."""
    with _presence_lock:
        if old_name in _presence:
            _presence[new_name] = _presence.pop(old_name)
        if old_name in _activity:
            _activity[new_name] = _activity.pop(old_name)
        if old_name in _activity_ts:
            _activity_ts[new_name] = _activity_ts.pop(old_name)
        _renamed_from.add(old_name)  # suppress leave message for old name
    with _cursors_lock:
        if old_name in _cursors:
            _cursors[new_name] = _cursors.pop(old_name)
    with _last_read_lock:
        if old_name in _last_read_channel:
            _last_read_channel[new_name] = _last_read_channel.pop(old_name)
    _save_cursors()


def purge_identity(name: str):
    """Remove all runtime state for a deregistered agent (presence, activity, cursors)."""
    with _presence_lock:
        _presence.pop(name, None)
        _activity.pop(name, None)
        _activity_ts.pop(name, None)
    with _cursors_lock:
        _cursors.pop(name, None)
    with _last_read_lock:
        _last_read_channel.pop(name, None)
    _save_cursors()


def migrate_cursors_rename(old_name: str, new_name: str):
    """Move cursor and fallback entries from old channel to new channel."""
    with _cursors_lock:
        for agent_cursors in _cursors.values():
            if old_name in agent_cursors:
                agent_cursors[new_name] = agent_cursors.pop(old_name)
    with _last_read_lock:
        for sender, fallback in list(_last_read_channel.items()):
            if fallback == old_name:
                _last_read_channel[sender] = new_name
    _save_cursors()


def migrate_cursors_delete(channel: str):
    """Remove cursors and reset every stale fallback to #general."""
    with _cursors_lock:
        for agent_cursors in _cursors.values():
            agent_cursors.pop(channel, None)
    with _last_read_lock:
        for sender, fallback in list(_last_read_channel.items()):
            if fallback == channel:
                _last_read_channel[sender] = "general"
    _save_cursors()


def _update_cursor(sender: str, msgs: list[dict], channel: str | None):
    if sender and msgs:
        ch_key = channel if channel else "__all__"
        with _cursors_lock:
            agent_cursors = _cursors.setdefault(sender, {})
            agent_cursors[ch_key] = msgs[-1]["id"]
        _save_cursors()


def chat_read(
    sender: str = "",
    since_id: int = 0,
    limit: int = 20,
    channel: str = "",
    ctx: Context | None = None,
) -> str:
    """Read chat messages. Returns JSON array with: id, sender, text, type, time, channel.

    Smart defaults:
    - First call with sender: returns last `limit` messages (full context).
    - Subsequent calls with same sender: returns only NEW messages since last read.
    - Pass since_id to override and read from a specific point.
    - Omit sender to always get the last `limit` messages (no cursor).
    - Pass channel to filter by channel name (default: all channels)."""
    sender, err = _resolve_tool_identity(sender, ctx, field_name="sender", required=False)
    if err:
        return err
    if channel and channel_guard and not channel_guard(channel):
        return f"Error: #{channel} is not a chat in the current project."

    ch = channel if channel else None
    # Remember the channel this agent just read so chat_send without an
    # explicit channel defaults here instead of falling back to "general".
    # Only record when a specific channel was requested — broad reads
    # (no channel) shouldn't overwrite a useful last-read.
    if sender and ch:
        with _last_read_lock:
            _last_read_channel[sender] = ch
    if since_id:
        msgs = store.get_since(since_id, channel=ch)
    elif sender:
        ch_key = ch if ch else "__all__"
        with _cursors_lock:
            agent_cursors = _cursors.get(sender, {})
            cursor = agent_cursors.get(ch_key, 0)
        if cursor:
            msgs = store.get_since(cursor, channel=ch)
        else:
            msgs = store.get_recent(limit, channel=ch)
    else:
        msgs = store.get_recent(limit, channel=ch)

    msgs = _in_project(msgs)[-limit:]
    _update_cursor(sender, msgs, ch)
    serialized = _serialize_messages(msgs)

    # Escalating empty-read hints to discourage polling loops
    if not serialized and sender:
        _empty_read_count[sender] = _empty_read_count.get(sender, 0) + 1
        n = _empty_read_count[sender]
        if n == 1:
            serialized = "No new messages. Do not poll — wait for your next prompt."
        elif n == 2:
            serialized = ("No new messages. You have read with no results twice — "
                          "stop polling and wait for a trigger.")
        else:
            serialized = ("No new messages. STOP. Repeated empty reads waste tokens. "
                          "Wait for your next prompt.")
    elif sender:
        _empty_read_count[sender] = 0

    # Prepend identity breadcrumb if multi-instance
    if sender and registry and registry.is_registered(sender):
        multi = registry.family_instance_count(sender) >= 2
        if multi:
            inst = registry.get_instance(sender)
            if inst:
                breadcrumb = f"[identity: {inst['name']} | label: {inst['label']}]"
                serialized = f"{breadcrumb}\n{serialized}"
    return serialized


def chat_resync(
    sender: str,
    limit: int = 50,
    channel: str = "",
    ctx: Context | None = None,
) -> str:
    """Explicit full-context fetch.

    Returns the latest `limit` messages and resets the sender cursor
    to the latest returned message id.
    Pass channel to filter by channel name (default: all channels).
    """
    sender, err = _resolve_tool_identity(sender, ctx, field_name="sender", required=True)
    if err:
        return err
    if channel and channel_guard and not channel_guard(channel):
        return f"Error: #{channel} is not a chat in the current project."
    ch = channel if channel else None
    msgs = _in_project(store.get_recent(limit, channel=ch))
    _update_cursor(sender, msgs, ch)
    serialized = _serialize_messages(msgs)
    return serialized


def _in_project(msgs: list[dict]) -> list[dict]:
    """Agents only see the active project's chats; other projects and legacy history stay out."""
    if not channel_guard:
        return msgs
    owned = {}
    return [m for m in msgs if owned.setdefault(m.get("channel", "general"), channel_guard(m.get("channel", "general")))]


def chat_join(name: str, channel: str = "general", ctx: Context | None = None) -> str:
    """Announce that you've connected to ZeroCollab."""
    name, err = _resolve_tool_identity(name, ctx, field_name="name", required=True, acting=True)
    if err:
        return err
    # Block pending instances (identity not yet confirmed)
    if registry and registry.is_pending(name):
        return "Error: identity not confirmed. Call chat_claim(sender=your_base_name) to get your identity."
    # Block base family names when multi-instance is active
    # (but allow if name is a registered+active instance — e.g. slot-1 'claude' that already claimed)
    if registry and name in registry.get_bases() and registry.family_instance_count(name) >= 2:
        inst = registry.get_instance(name)
        if not inst or inst.get("state") != "active":
            return (f"Error: multiple {name} instances registered. "
                    f"Call chat_claim(sender='{name}') to get your unique identity first.")
    # Block unregistered agent names (stale identity from resumed session)
    if registry and registry.is_agent_family(name) and not registry.is_registered(name):
        return f"Error: '{name}' is not registered. Call chat_claim(sender=your_base_name) to get your identity."
    store.add(name, f"{name} is online", msg_type="join", channel="general")
    online = _get_online()
    return f"Joined. Online: {', '.join(online)}"


def chat_who() -> str:
    """Check who's currently online in ZeroCollab."""
    online = _get_online()
    return f"Online: {', '.join(online)}" if online else "Nobody online."


def _touch_presence(name: str):
    """Update presence timestamp — called on any MCP tool use."""
    with _presence_lock:
        _presence[name] = time.time()


def _get_online() -> list[str]:
    now = time.time()
    with _presence_lock:
        return [name for name, ts in _presence.items()
                if now - ts < PRESENCE_TIMEOUT]


def is_online(name: str) -> bool:
    now = time.time()
    with _presence_lock:
        return name in _presence and now - _presence.get(name, 0) < PRESENCE_TIMEOUT


def set_active(name: str, active: bool):
    with _presence_lock:
        _activity[name] = active
        if active:
            _activity_ts[name] = __import__("time").time()


def is_active(name: str) -> bool:
    import time as _time
    with _presence_lock:
        if not _activity.get(name, False):
            return False
        # Auto-expire stale activity
        ts = _activity_ts.get(name, 0)
        if _time.time() - ts > ACTIVITY_TIMEOUT:
            _activity[name] = False
            return False
        return True


def chat_claim(sender: str, name: str = "", ctx: Context | None = None) -> str:
    """Claim your identity in a multi-instance setup.

    - Without name: accept the auto-assigned identity and unlock chat_send.
    - With name: reclaim a previous identity (e.g. from a breadcrumb after /resume).

    Your sender must be your current registered name (the one assigned at registration).
    The identity breadcrumb in chat_read responses shows your current identity."""
    sender, err = _resolve_tool_identity(sender, ctx, field_name="sender", required=True, acting=True)
    if err:
        return err
    if not registry:
        return "Error: registry not available."
    target = name.strip() if name.strip() else None
    result = registry.claim(sender, target)
    if isinstance(result, str):
        return f"Error: {result}"
    # Touch presence with the CONFIRMED name (may differ from sender)
    confirmed = result.get("name", sender)
    _touch_presence(confirmed)
    return json.dumps({"confirmed_name": confirmed, "label": result.get("label", ""), "base": result.get("base", "")})


def chat_channels() -> str:
    """List all available channels. Returns a JSON array of channel names."""
    channels = room_settings.get("channels", ["general"]) if room_settings else ["general"]
    if channel_guard:
        channels = [c for c in channels if channel_guard(c)]
    return json.dumps(channels)


_ALL_TOOLS = [
    chat_send, chat_read, chat_resync, chat_join, chat_who, chat_channels, chat_claim,
]


def _create_server(port: int) -> FastMCP:
    server = FastMCP(
        "zerocollab",
        host="127.0.0.1",
        port=port,
        log_level="ERROR",
        instructions=_MCP_INSTRUCTIONS,
    )
    for func in _ALL_TOOLS:
        @__import__('functools').wraps(func)
        def guarded(*args, __fn=func, **kwargs):
            if deployment:
                return deployment.guard_tool(__fn)(*args, **kwargs)
            return __fn(*args, **kwargs)
        server.tool()(guarded)
    return server


mcp_http = _create_server(8200)  # streamable-http for Claude/Codex/Qwen
mcp_sse = _create_server(8201)   # SSE for Gemini

# Keep backward compat — run.py references mcp_bridge.store
# (store is set by run.py before starting)


def run_http_server():
    """Block — run streamable-http MCP in a background thread."""
    mcp_http.run(transport="streamable-http")


def run_sse_server():
    """Block — run SSE MCP in a background thread."""
    mcp_sse.run(transport="sse")

