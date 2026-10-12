"""ZeroCollab - FastAPI web UI + agent auto-trigger."""

import asyncio
import hmac
import json
import re as _re
import sys
import threading
import uuid
import logging
import weakref
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.requests import Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from store import MessageStore
from router import Router
from agents import AgentTrigger
from registry import RuntimeRegistry
from durable import atomic_write

log = logging.getLogger(__name__)

app = FastAPI(title="ZeroCollab")

# --- globals (set by configure()) ---
store: MessageStore | None = None
router: Router | None = None
agents: AgentTrigger | None = None
registry: RuntimeRegistry | None = None
config: dict = {}
ws_clients: set[WebSocket] = set()
# Locks live only as long as their sockets; broadcasts must retain per-client order.
_ws_send_locks = weakref.WeakKeyDictionary()
WS_SEND_TIMEOUT_SECONDS = 5
WS_CLOSE_TIMEOUT_SECONDS = 1

# --- Security: session token (set by configure()) ---
session_token: str = ""
# Browser sign-in for the web room (ui_sessions.BrowserSessions), or None: the desktop app keeps the
# token-in-preload model and passes none.
browser = None
# Set by room_runtime.install(); its running turns hold the run-scoped capability tokens.
runtime = None

# Room settings (persisted to data/settings.json)
PRODUCT_TITLE = "ZeroCollab"
room_settings: dict = {
    "title": PRODUCT_TITLE,
    # Shown beside the title, so several servers can be told apart without
    # renaming the product itself. "" hides it.
    "subtitle": "",
    "username": "user",
    "font": "sans",
    "channels": ["general"],
    "history_limit": "all",
    "contrast": "normal",
}

# Channel validation
_CHANNEL_NAME_RE = _re.compile(r'^[a-z0-9][a-z0-9\-]{0,19}$')

def _settings_path() -> Path:
    data_dir = config.get("server", {}).get("data_dir", "./data")
    return Path(data_dir) / "settings.json"


ROOM_SUBTITLE_MAX = 40


def normalize_room_subtitle(value):
    """Clean a room subtitle, or return None if it is not usable.

    Collapses any internal whitespace so a pasted newline cannot break the
    header across two lines, and caps the length so a long name cannot crowd
    out the rest of the header.
    """
    if not isinstance(value, str):
        return None
    return " ".join(value.split())[:ROOM_SUBTITLE_MAX]


def apply_room_subtitle(new: dict):
    """Apply a subtitle from a settings payload, if present and usable."""
    if "subtitle" not in new:
        return
    cleaned = normalize_room_subtitle(new["subtitle"])
    if cleaned is not None:
        room_settings["subtitle"] = cleaned


def _load_settings():
    global room_settings
    p = _settings_path()
    if p.exists():
        try:
            saved = json.loads(p.read_text("utf-8"))
            room_settings.update(saved)
        except Exception:
            pass
    # Settings for removed features (roles, rule refresh) are dropped on load.
    for legacy_key in ("custom_roles", "rules_refresh_interval"):
        room_settings.pop(legacy_key, None)
    # settings.json is hand-editable, so the file bypasses every check the
    # live update path applies. Re-normalise on load rather than trust it.
    room_settings["subtitle"] = normalize_room_subtitle(room_settings.get("subtitle")) or ""
    # Ensure "general" always exists and is first
    if "channels" not in room_settings or not room_settings["channels"]:
        room_settings["channels"] = ["general"]
    elif "general" not in room_settings["channels"]:
        room_settings["channels"].insert(0, "general")


def _save_settings():
    content = json.dumps(room_settings, indent=2)
    atomic_write(_settings_path(), lambda stream: stream.write(content))


def _runtime():
    return getattr(agents, 'runtime', None)


def settings_payload() -> dict:
    """Saved room settings plus the active project's chats (never persisted to settings.json).
    'channels' stays the room-wide union of channel ids for older clients."""
    runtime = _runtime()
    if not runtime or runtime.project_room.path is None:
        return room_settings
    import project_registry
    return {**room_settings, **project_registry.sessions_payload(runtime)}


def _channel_refusal(channel: str) -> str:
    """'' if the active project may post in this chat, else the reason it may not."""
    runtime = _runtime()
    if not runtime or runtime.owns_channel(channel):
        return ""
    return "That chat belongs to another project or is read-only history. Choose a chat in the current project."


def _stale_project(event: dict) -> str:
    runtime = _runtime()
    project_id = event.get("project_id")
    if runtime and project_id and project_id != runtime.identity:
        return "The project changed; reload the chats for the current project."
    return ""


def _extract_agent_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-agent-token", "").strip()


def _resolve_authenticated_agent(request: Request) -> dict | None:
    if not registry:
        return None
    token = _extract_agent_token(request)
    if not token:
        return None
    return registry.resolve_token(token)


# --- Security middleware ---
# Paths that don't require the session token (public assets).
_PUBLIC_PREFIXES = ("/", "/static/")


def _token_matches(supplied, expected: str) -> bool:
    return bool(supplied is not None and expected
                and hmac.compare_digest(supplied.encode(), expected.encode()))


def _run_token_allows(token, method: str, path: str) -> bool:
    """A run's capability token reaches only its own coordination route and image upload,
    and only while that run is active. Everything else, including /ws, needs the UI token."""
    if not token or method != "POST" or runtime is None:
        return False
    run = next((r for r in list(runtime.running.values())
                if _token_matches(token, getattr(r, "room_token", None) or "")), None)
    return run is not None and path in ("/api/upload", f"/api/runtime/coordination/{run.id}")


def _install_security_middleware(token: str, cfg: dict, sessions=None):
    """Add token validation and origin checking middleware to the app. With sessions (the web room),
    browsers sign in by cookie at the room host instead of reading the token from the page."""
    import app as _self
    if sessions is not None:
        import ui_sessions  # web room only; the desktop app's file list need not carry it
    _self.session_token = token
    _self.browser = sessions
    port = cfg.get("server", {}).get("port", 8300)
    allowed_origins = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
    }
    loopback_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    if sessions is not None:
        allowed_origins.add(sessions.origin)

    def forbidden(message):
        return JSONResponse({"error": f"forbidden: {message}"}, status_code=403)

    class SecurityMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            host = request.headers.get("host", "")

            if sessions is not None:
                # A browser opening the room by its address is sent to the room host, where its cookie lives.
                # Only navigations: scripts such as Start-Room still read '/' here to recognise the room.
                if (path == "/" and request.method == "GET" and host in loopback_hosts
                        and request.headers.get("sec-fetch-mode") == "navigate"):
                    return RedirectResponse(sessions.origin + "/", status_code=302)
                if path == "/launch":
                    if sessions.at_room(host):
                        return await call_next(request)
                    if request.method == "GET" and host in loopback_hosts:
                        return RedirectResponse(sessions.origin + "/launch", status_code=302)
                    return forbidden("sign in at the room address")

            # Static assets, index page, and uploaded images are public.
            # The index page injects the token client-side via same-origin script.
            # Uploads use random filenames and have path-traversal protection.
            if path == "/" or path.startswith(("/static/", "/uploads/")):
                return await call_next(request)

            # Agent registration/heartbeat: loopback only (no remote agent minting).
            if path.startswith(("/api/register", "/api/deregister/", "/api/heartbeat/")):
                client_ip = request.client.host if request.client else ""
                if client_ip not in ("127.0.0.1", "::1", "localhost"):
                    return JSONResponse(
                        {"error": f"forbidden: agent registration is restricted to local loopback. Source {client_ip} is not allowed."},
                        status_code=403,
                    )
                return await call_next(request)

            # The API key relay (provider_relay.py) is gated only by its own checks: it refuses any
            # Origin header, needs a loopback client and exact Host, and a relay token of a running
            # turn. Neither the UI token nor a run's room token opens it, and it opens nothing else.
            if path.startswith("/relay/"):
                return await call_next(request)

            # --- Origin check (blocks cross-origin / DNS-rebinding attacks) ---
            origin = request.headers.get("origin")
            if origin and origin not in allowed_origins:
                return JSONResponse(
                    {"error": "forbidden: origin not allowed"},
                    status_code=403,
                )

            # --- Browser session (web room only) ---
            # Valid only at the exact room host, so a cookie replayed to 127.0.0.1 or another port never counts,
            # and never for the routes that mint launch codes or list sessions (UI-token header only).
            if (sessions is not None and sessions.at_room(host) and path not in ui_sessions.HEADER_ONLY_PATHS
                    and sessions.valid(request.cookies.get(ui_sessions.COOKIE))):
                origin_ok = origin == sessions.origin
                if origin and not origin_ok:
                    return forbidden("origin not allowed")
                if request.method not in ("GET", "HEAD") and not origin_ok:
                    return forbidden("missing origin")
                # Same-site pages on the room host's other ports can send credentialed requests; only the
                # room's own page counts, so even a GET from them is refused.
                fetch_site = request.headers.get("sec-fetch-site")
                if fetch_site and fetch_site != "same-origin":
                    return forbidden("cross-site request")
                return await call_next(request)

            # --- Token check ---
            # Allow registered agents to authenticate via Bearer token
            # for /api/messages and /api/send (no browser session needed).
            auth_header = request.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer ") and path in ("/api/messages", "/api/send"):
                bearer = auth_header[7:].strip()
                if _self.registry and _self.registry.resolve_token(bearer):
                    return await call_next(request)

            # The web room takes the token from the header only; a query parameter put it into URLs.
            req_token = (
                request.headers.get("x-session-token")
                or (None if sessions is not None else request.query_params.get("token"))
            )
            if not _token_matches(req_token, _self.session_token) and not _self._run_token_allows(
                    request.headers.get("x-session-token"), request.method, path):
                return JSONResponse(
                    {"error": "forbidden: invalid or missing session token"},
                    status_code=403,
                )

            return await call_next(request)

    app.add_middleware(SecurityMiddleware)
    # Added last, so it runs first, for HTTP and WebSocket alike. A DNS-rebinding page reaches this
    # server under its own domain name. GETs from it carry no Origin header, and '/' embeds the
    # session token, so only the Host header tells it apart from the user's own browser tab; the
    # Origin and token checks above cannot protect '/'.
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    server = cfg.get("server", {})
    bracket = lambda name: f"[{name}]" if ":" in name else name  # Starlette compares IPv6 hosts with their brackets
    bind = str(server.get("host", "127.0.0.1")).strip("[]")
    extra = server.get("allowed_hosts", [])
    if not isinstance(extra, list) or not all(isinstance(h, str) and h and "*" not in h for h in extra):
        raise ValueError('server.allowed_hosts must be a list of host names or addresses, without wildcards')
    hosts = ["127.0.0.1", "localhost", "[::1]", *(bracket(h.strip("[]")) for h in extra)]
    if bind in ("0.0.0.0", "::"):
        # Every interface: the names other computers use cannot be inferred, and accepting any Host
        # would reopen DNS rebinding, so only loopback and the listed names are served.
        if not extra:
            log.warning('server.host is %s but server.allowed_hosts is empty, so only this computer can open the room. '
                        'List the names or addresses other computers use, e.g. allowed_hosts = ["192.168.1.20", "studio.local"]', bind)
    elif bracket(bind) not in hosts:
        hosts.append(bracket(bind))
    if sessions is not None:
        hosts.append(ui_sessions.ROOM_HOST)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts, www_redirect=False)


def _install_browser_routes(sessions):
    """Launch codes and the sign-in exchange (web room only). Minting codes and listing session hashes
    need the UI token in the header: the middleware never lets a cookie reach these two routes, and
    they check the header again here."""
    import ui_sessions

    def header_token(request: Request) -> bool:
        return _token_matches(request.headers.get("x-session-token"), session_token)

    async def launch_codes(request: Request):
        if not header_token(request):
            return JSONResponse({"error": "forbidden: UI token required"}, status_code=403)
        return {"code": sessions.mint(), "expires_in": ui_sessions.CODE_SECONDS}

    async def ui_session_hashes(request: Request):
        if not header_token(request):
            return JSONResponse({"error": "forbidden: UI token required"}, status_code=403)
        return {"sessions": sessions.hand_over()}

    async def launch_page():
        return HTMLResponse(ui_sessions.LAUNCH_HTML, headers=ui_sessions.LAUNCH_HEADERS)

    async def launch_exchange(request: Request):
        # The middleware admitted only the exact room host; the page itself must be the sender.
        if request.headers.get("origin") != sessions.origin:
            return JSONResponse({"error": ui_sessions.REFUSED}, status_code=403, headers={"Cache-Control": "no-store"})
        try:
            body = await request.json()
        except ValueError:
            body = None
        secret = sessions.redeem(body.get("code") if isinstance(body, dict) else None)
        if secret is None:
            return JSONResponse({"error": ui_sessions.REFUSED}, status_code=403, headers={"Cache-Control": "no-store"})
        response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        # Host-only (no Domain) and no Max-Age/Expires: a session cookie at the room host only.
        response.set_cookie(ui_sessions.COOKIE, secret, httponly=True, samesite="strict", path="/")
        return response

    app.add_api_route("/api/runtime/launch-codes", launch_codes, methods=["POST"])
    app.add_api_route("/api/runtime/ui-sessions", ui_session_hashes, methods=["GET"])
    app.add_api_route("/launch", launch_page, methods=["GET"])
    app.add_api_route("/launch", launch_exchange, methods=["POST"])


def _browser_alert(text: str):
    """The revoke-on-reuse alert, in the room's main chat. It names no code or session."""
    if store is None:
        return
    try:
        channel = runtime.default_channel() if runtime is not None else "general"
    except Exception:
        channel = "general"
    store.add("system", text, channel=channel, metadata={"no_route": True})


def configure(cfg: dict, session_token: str = "", sessions=None):
    global store, router, agents, registry, config
    config = cfg
    # --- Security: store the session token and install middleware ---
    _install_security_middleware(session_token, cfg, sessions)
    if sessions is not None:
        sessions.alert = _browser_alert
        _install_browser_routes(sessions)

    data_dir = cfg.get("server", {}).get("data_dir", "./data")
    Path(data_dir).mkdir(parents=True, exist_ok=True)

    log_path = Path(data_dir) / "zerocollab_log.jsonl"
    legacy_log_path = Path(data_dir) / "room_log.jsonl"
    if not log_path.exists() and legacy_log_path.exists():
        # Backward compatibility for existing installs.
        log_path = legacy_log_path

    store = MessageStore(str(log_path))
    # Initialize store upload dir from config
    raw_upload_dir = cfg.get("images", {}).get("upload_dir", "./uploads")
    store.upload_dir = Path(raw_upload_dir)

    max_hops = cfg.get("routing", {}).get("max_agent_hops", 4)

    # Registry: single source of truth for all live agent state
    registry = RuntimeRegistry(data_dir=data_dir)
    registry.seed(cfg.get("agents", {}))
    registry.on_change(_on_registry_change)

    # Router starts with base agent names (backward compat for direct MCP users),
    # registry.on_change updates it dynamically when instances register/deregister
    agent_names = list(cfg.get("agents", {}).keys())
    router = Router(
        agent_names=agent_names,
        default_mention=cfg.get("routing", {}).get("default", "none"),
        max_hops=max_hops,
        online_checker=lambda: set(registry.get_active_names()) if registry else set(),
    )
    agents = AgentTrigger(registry, data_dir=data_dir)

    # Bridge: when ANY message is added to store (including via MCP),
    # broadcast to all WebSocket clients
    store.on_message(_on_store_message)

    _load_settings()

    # Apply saved loop guard setting
    if "max_agent_hops" in room_settings:
        router.max_hops = room_settings["max_agent_hops"]

    # Background thread: check for wrapper recovery flag files
    _data_dir = Path(data_dir)

    _known_online: set[str] = set()  # agents we've seen join — track for leave messages
    _posted_leave: set[str] = set()  # agents we've already posted a leave for — debounce

    _known_active = set()

    def _background_checks():
        import time as _time
        import mcp_bridge

        while True:
            _time.sleep(3)
            gate = getattr(_runtime(), 'deployment', None)
            if gate and not gate.enter_mutation():
                continue
            try:
                # Recovery flags
                try:
                    for flag in _data_dir.glob("*_recovered"):
                        agent_name = flag.read_text("utf-8").strip()
                        flag.unlink()
                        store.add(
                            "system",
                            f"Agent routing for {agent_name} interrupted — auto-recovered. "
                            "If agents aren't responding, try sending your message again."
                        )
                except Exception:
                    pass

                # Pending instances (slot 2+) wait for human naming or agent claim.
                # No auto-confirm — identity must be explicitly resolved.

                # Presence expiry — post leave messages (but do NOT deregister).
                # Deregistration only happens via /api/deregister (wrapper shutdown)
                # OR the 60s crash timeout below.
                # Short timeout (10s) prevents slot theft when MCP tool calls are intermittent.
                try:
                    now = _time.time()
                    with mcp_bridge._presence_lock:
                        currently_online = {
                            name for name, ts in mcp_bridge._presence.items()
                            if now - ts < mcp_bridge.PRESENCE_TIMEOUT
                        }
                        currently_active = set()
                        for name, active in mcp_bridge._activity.items():
                            if active:
                                if now - mcp_bridge._activity_ts.get(name, 0) < mcp_bridge.ACTIVITY_TIMEOUT:
                                    currently_active.add(name)
                                else:
                                    mcp_bridge._activity[name] = False  # auto-expire

                    # Room-managed agents are owned by the runtime, not by a wrapper heartbeat (room_runtime
                    # touches them every 3 s). A stalled event loop must not make the whole team look offline,
                    # nor deregister them as crashed (harness 62f2ad53; they are registry instances too).
                    managed = set()
                    runtime_now = _runtime()
                    if runtime_now:
                        try:
                            managed = {n for n, s in list(runtime_now.states.items()) if not s.get('archived')}
                        except RuntimeError:
                            pass  # states changed mid-copy; the next pass catches up
                    currently_online |= managed

                    # Crash timeout: if a wrapper hasn't heartbeated for 60s,
                    # it's dead — deregister it to free the slot.
                    _CRASH_TIMEOUT = 15
                    registered = set(registry.get_all_names())
                    for name in registered:
                        if name in managed:
                            continue
                        with mcp_bridge._presence_lock:
                            last_seen = mcp_bridge._presence.get(name, 0)
                        if last_seen > 0 and now - last_seen > _CRASH_TIMEOUT:
                            log.info(f"Crash timeout: deregistering {name} (no heartbeat for {_CRASH_TIMEOUT}s)")
                            # Sleep / crash path: keep the token reclaimable so the
                            # wrapper transparently recovers its identity on wake.
                            # (A deliberate /api/deregister below stays non-reclaimable.)
                            result = registry.deregister(name, reclaimable=True)
                            if result:
                                mcp_bridge.purge_identity(name)
                                registry.clean_renames_for(name)
                                renamed = result.get("_renamed_back")
                                if renamed:
                                    mcp_bridge.migrate_identity(renamed["old"], renamed["new"])
                                    store.rename_sender(renamed["old"], renamed["new"])
                                    _migrate_agent_last_channel(renamed["old"], renamed["new"])
                                    if _event_loop:
                                        rename_event = json.dumps({
                                            "type": "agent_renamed",
                                            "old_name": renamed["old"],
                                            "new_name": renamed["new"],
                                        })
                                        asyncio.run_coroutine_threadsafe(_broadcast(rename_event), _event_loop)
                                store.add(name, f"{name} disconnected (timeout)", msg_type="leave", channel=_agent_last_channel.get(name, _last_active_channel))
                                _posted_leave.add(name)

                    # Re-fetch registered names (may have changed from crash timeout above)
                    registered = set(registry.get_all_names())

                    # Detect registered instances going offline (leave message only)
                    timed_out = registered - currently_online
                    for name in timed_out:
                        inst = registry.get_instance(name)
                        if not inst:
                            continue
                        # Skip names that were just renamed (not actually offline)
                        with mcp_bridge._presence_lock:
                            was_renamed = name in mcp_bridge._renamed_from
                            if was_renamed:
                                mcp_bridge._renamed_from.discard(name)
                        if was_renamed:
                            continue
                        # Post leave message ONCE per offline transition (debounced)
                        if name not in _posted_leave:
                            _posted_leave.add(name)
                            store.add(name, f"{name} disconnected", msg_type="leave", channel=_agent_last_channel.get(name, _last_active_channel))

                    # Clear leave debounce for agents that came back online
                    _posted_leave.difference_update(currently_online)

                    # Detect other agents (non-registered) going offline
                    went_offline = (_known_online - currently_online) - timed_out
                    for name in went_offline:
                        # Skip leave messages for names that were just renamed
                        with mcp_bridge._presence_lock:
                            was_renamed = name in mcp_bridge._renamed_from
                            if was_renamed:
                                mcp_bridge._renamed_from.discard(name)
                        if was_renamed:
                            continue
                        if not registry.is_registered(name) and name not in _posted_leave:
                            _posted_leave.add(name)
                            store.add(name, f"{name} disconnected", msg_type="leave", channel=_agent_last_channel.get(name, _last_active_channel))

                    if _known_online != currently_online and _event_loop:
                        asyncio.run_coroutine_threadsafe(broadcast_status(), _event_loop)

                    # Clear stale activity for agents that went offline
                    with mcp_bridge._presence_lock:
                        stale_active = [n for n in mcp_bridge._activity
                                        if mcp_bridge._activity.get(n) and n not in currently_online]
                        for n in stale_active:
                            mcp_bridge._activity[n] = False
                        if stale_active:
                            currently_active -= set(stale_active)

                    # Broadcast status on any change (online set or activity set)
                    if currently_active != _known_active or _known_online != currently_online:
                        _known_active.clear()
                        _known_active.update(currently_active)
                        if _event_loop:
                            asyncio.run_coroutine_threadsafe(broadcast_status(), _event_loop)
                    _known_online.clear()
                    _known_online.update(currently_online)
                except Exception:
                    pass
            finally:
                if gate:
                    gate.leave_mutation()

    threading.Thread(target=_background_checks, daemon=True).start()


# --- Store → WebSocket bridge ---

_event_loop = None  # set by run.py after starting the event loop
_last_active_channel: str = "general"  # last channel any message was sent in
# Per-agent last channel: where each sender was most recently active. Used to
# route leave/disconnect messages to the channel that agent was talking in,
# instead of the global last-active channel (which is usually #general and made
# leave spam land in the wrong place).
_agent_last_channel: dict[str, str] = {}


def _migrate_agent_last_channel(old_name: str, new_name: str):
    """Re-key _agent_last_channel across an agent rename.

    Without this, a disconnect for the post-rename name misses the map and
    falls back to the global last-active channel — putting the leave message
    back in the wrong channel. Call this beside every migrate_identity /
    rename_sender path (slot-1 rename, renamed-back, websocket rename_agent,
    /api/label)."""
    if old_name == new_name:
        return
    ch = _agent_last_channel.pop(old_name, None)
    if ch is not None:
        _agent_last_channel[new_name] = ch


def set_event_loop(loop):
    global _event_loop
    _event_loop = loop


def _on_store_message(msg: dict):
    """Called from any thread when a message is added to the store."""
    if _event_loop is None:
        return
    gate = getattr(_runtime(), 'deployment', None)
    if gate:
        gate.track_continuation()
    async def routed():
        try:
            await _handle_new_message(msg)
        finally:
            if gate:
                gate.leave_mutation()
    try:
        # If called from the event loop thread (e.g. WebSocket handler),
        # schedule directly as a task
        loop = asyncio.get_running_loop()
        if loop is _event_loop:
            asyncio.ensure_future(routed())
            return
    except RuntimeError:
        pass  # No running loop — we're in a different thread (MCP)
    try:
        asyncio.run_coroutine_threadsafe(routed(), _event_loop)
    except RuntimeError:
        if gate:
            gate.leave_mutation()
        raise


async def _handle_new_message(msg: dict):
    """Broadcast message to web clients + check for @mention triggers."""
    text = msg.get("text", "")
    msg_type = msg.get("type", "chat")
    sender = msg.get("sender", "")
    channel = msg.get("channel", "general")

    # Track last active channel for leave/join messages (skip system messages)
    global _last_active_channel
    if msg_type not in ("system", "leave", "join"):
        _last_active_channel = channel
        # Remember where this specific sender was last active so their
        # disconnect message follows them to the right channel.
        if sender and sender != "system":
            _agent_last_channel[sender] = channel

    await broadcast(msg)
    if msg.get('metadata', {}).get('no_route'):
        return
    # Agents belong to the active project; other projects' chats and legacy history never wake them.
    managed_runtime = getattr(agents, 'runtime', None)
    if managed_runtime and not managed_runtime.owns_channel(channel):
        return

    # System messages never trigger routing - prevents infinite callback loops
    if sender == "system":
        return

    # A live team-decision chat is driven by its flow: a human message there is new direction, and
    # agent text never wakes anyone, so @mentions cannot bypass the flow's tokens and epochs.
    if managed_runtime and not managed_runtime.decisions.routes(channel):
        agent_names = set(managed_runtime.states) | set(config.get("agents", {}))
        if registry:
            agent_names.update(registry.get_all_names())
        if sender not in agent_names:
            async with managed_runtime.decisions.lock:
                managed_runtime.decisions.human_message(channel, msg.get('id'))
        return

    known_agents = set(registry.get_all_names()) if registry else set()
    known_agents.update(config.get("agents", {}).keys())

    chain = managed_runtime.routing_chain(sender, channel, msg.get('metadata'), text) if managed_runtime else None
    if chain is False:
        store.add('system', 'Agent handoff budget reached. Send a new instruction to continue.', channel=channel)
        return
    managed_sender = managed_runtime and sender in managed_runtime.states
    raw_targets = router.get_targets(sender, text, channel, count_hop=not managed_sender)
    # Zero #12950: a human message with no @tag goes to the lead (response_claims.py), who answers,
    # delegates and wakes owners of actionable tasks; the claim is recorded before anyone is woken. A human
    # @all wakes the whole team, as before 0.12.26, and an explicit @agent wakes that agent.
    claim = None
    human = bool(managed_runtime) and not managed_sender and sender not in known_agents
    # An @all has no competing answer, so it supersedes nothing: a lead still answering an earlier
    # untagged message keeps its claim (harness f0e86c85 F1). Its targets that the message doesn't name
    # directly are marked broadcast, so their interim text stays in Activity (F2).
    team_wide = human and router.broadcast_kind(text) == 'all'
    named = set()
    if team_wide:
        for name in router.named_mentions(text):
            named.update(registry.resolve_to_instances(name) if registry else [name])
    elif human and router.untagged(text):
        claim = managed_runtime.response_claims.open(channel, msg.get('id'))
        raw_targets = [claim['owner']] if claim else []
    # Resolve base family names to actual registered instances
    # e.g. 'claude' → 'claude-prime' when slot-1 was renamed
    targets = []
    for t in raw_targets:
        if registry:
            targets.extend(registry.resolve_to_instances(t))
        else:
            targets.append(t)
    targets = list(dict.fromkeys(targets))  # dedupe, preserve order

    if not managed_sender and router.is_paused(channel):
        # Only emit the loop guard notice once per pause
        if not router.is_guard_emitted(channel):
            router.set_guard_emitted(channel)
            store.add(
                "system",
                f"Loop guard: {router.max_hops} agent-to-agent hops reached. "
                "Send a new message to resume.",
                channel=channel
            )
        return

    # Build a readable message string for the wake prompt
    chat_msg = f"{sender}: {text}" if text else ""
    sender_is_agent = sender in known_agents

    import mcp_bridge
    for target in targets:
        # Skip pending instances — they haven't been named/claimed yet
        if registry:
            inst = registry.get_instance(target)
            if inst and inst.get("state") == "pending":
                continue
        if not mcp_bridge.is_online(target):
            store.add("system", f"{target} appears offline — message queued.", msg_type="system", channel=channel)
        if agents.is_available(target):
            routing = {'message_id': msg.get('id'), 'human': not sender_is_agent,
                       **({'broadcast': True} if team_wide and target not in named else {})}
            message = chat_msg
            if claim and target == claim['owner']:
                routing.update(response_claim=claim['key'], generation=claim['generation'])
                # The lead answers for the team and gets work moving when that's called for (Zero #12950).
                message = ("You are the lead: this message from the user names nobody, so it came to you and nobody "
                           "else was woken. Give the one answer. If it means work should move, check the open tasks "
                           "listed in your context (room_task get for detail; never read the whole board) and mention "
                           "each agent with an unblocked next step, one line each with that step. Mention nobody who "
                           "has nothing to do.\n\n" + chat_msg)
            queued = await agents.trigger(target, message=message, channel=channel, chain=chain, routing=routing)
            if queued is False:
                store.add("system", f"Could not queue {target}'s wake. Retry the mention when its wrapper is available.",
                          msg_type="system", channel=channel, metadata={'no_route': True})


# --- broadcasting ---

async def _broadcast(raw_json: str):
    """Deliver in order per socket without making healthy clients wait for a slow one."""
    async def send(client):
        lock = _ws_send_locks.setdefault(client, asyncio.Lock())
        async with lock:
            if client not in ws_clients:
                return
            try:
                await asyncio.wait_for(client.send_text(raw_json), WS_SEND_TIMEOUT_SECONDS)
            except Exception:
                # Remove before releasing the lock so queued broadcasts skip this
                # socket. Closing lets the browser reconnect and reload history.
                ws_clients.discard(client)
                try:
                    await asyncio.wait_for(client.close(code=1013), WS_CLOSE_TIMEOUT_SECONDS)
                except Exception:
                    pass
    await asyncio.gather(*(send(client) for client in list(ws_clients)))


async def broadcast(msg: dict):
    data = json.dumps({"type": "message", "data": msg})
    await _broadcast(data)


async def broadcast_status():
    status = agents.get_status()
    status["paused"] = any(router.is_paused(ch) for ch in room_settings.get("channels", ["general"]))
    data = json.dumps({"type": "status", "data": status})
    await _broadcast(data)


async def broadcast_typing(agent_name: str, is_typing: bool):
    data = json.dumps({"type": "typing", "agent": agent_name, "active": is_typing})
    await _broadcast(data)


async def broadcast_clear(channel: str | None = None):
    payload = {"type": "clear"}
    if channel:
        payload["channel"] = channel
    data = json.dumps(payload)
    await _broadcast(data)


async def broadcast_session_created(session: dict, ref=None):
    runtime = _runtime()
    await _broadcast(json.dumps({"type": "session_created", "project_id": runtime.identity if runtime else "",
                                 "id": session["id"], "title": session["title"], "ref": ref}))


async def broadcast_settings():
    data = json.dumps({"type": "settings", "data": settings_payload()})
    await _broadcast(data)


async def broadcast_agents():
    """Send updated agent config (from registry) to all WebSocket clients."""
    agent_cfg = registry.get_agent_config() if registry else {}
    data = json.dumps({"type": "agents", "data": agent_cfg})
    await _broadcast(data)


def _on_registry_change():
    """Called from registry (any thread) when instances register/deregister/claim/rename."""
    # Update router with current agent names (base names + registered instances)
    if router and registry:
        base_names = list(registry.get_bases().keys())
        # Only include active instances in routing (pending ones are inert)
        instance_names = registry.get_active_names()
        all_names = list(set(base_names + instance_names))
        router.update_agents(all_names, aliases=registry.get_mention_aliases())
    # Broadcast to WebSocket clients
    if _event_loop:
        asyncio.run_coroutine_threadsafe(broadcast_agents(), _event_loop)
        asyncio.run_coroutine_threadsafe(broadcast_status(), _event_loop)


# --- WebSocket ---

# Liveness probe for the page's connection watchdog: {"type": "ping", "id": "<1-64 of A-Z a-z 0-9 _ ->"}
# gets {"type": "pong", "id": <same>} on the same socket.
_PING_ID = _re.compile(r'[A-Za-z0-9_-]{1,64}')

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # --- Security: validate session token on WebSocket connect ---
    # The desktop app's preload holds the token and passes it in the query; the web room's page signs in by
    # cookie, valid only at the room host and only from the room's own page.
    token = websocket.query_params.get("token", "")
    allowed = bool(token) and _token_matches(token, session_token)
    if not allowed and browser is not None:
        import ui_sessions
        allowed = (browser.at_room(websocket.headers.get("host", ""))
                   and websocket.headers.get("origin") == browser.origin
                   and browser.valid(websocket.cookies.get(ui_sessions.COOKIE)))
    if not allowed:
        # Must accept before closing so the browser receives the close frame.
        # Code 4003 triggers an auto-reload in the client to pick up the new token.
        await websocket.accept()
        await websocket.close(code=4003, reason="forbidden: invalid session token")
        return

    await websocket.accept()
    ws_clients.add(websocket)

    # Send settings
    await websocket.send_text(json.dumps({"type": "settings", "data": settings_payload()}))

    # Send registered instances (used for pills/mentions)
    agent_cfg = registry.get_agent_config() if registry else {}
    await websocket.send_text(json.dumps({"type": "agents", "data": agent_cfg}))

    # Send base agent colors (used for message coloring, no pills)
    base_colors = {}
    for name, cfg in config.get("agents", {}).items():
        base_colors[name] = {"color": cfg.get("color", "#888"), "label": cfg.get("label", name)}
    await websocket.send_text(json.dumps({"type": "base_colors", "data": base_colors}))

    # Send pending instances (so late-connecting browsers still see the naming lightbox)
    if registry:
        for inst in registry.get_all().values():
            if inst.get("state") == "pending":
                await websocket.send_text(json.dumps({
                    "type": "pending_instance",
                    "name": inst["name"],
                    "base": inst.get("base", ""),
                    "label": inst.get("label", inst["name"]),
                    "color": inst.get("color", "#888"),
                }))

    # Send history (per channel based on history_limit)
    limit_val = room_settings.get("history_limit", "all")
    count = 10000 if limit_val == "all" else int(limit_val)
    
    history = []
    for ch in room_settings["channels"]:
        history.extend(store.get_recent(count, channel=ch))
    
    # Sort history by timestamp to interleave messages from different channels correctly
    history.sort(key=lambda m: m.get("timestamp", 0))
    
    # Saved messages have their own boundary; presence updates can arrive at
    # any time and must never make old history look like new chat.
    for offset in range(0, len(history), 100):
        await websocket.send_text(json.dumps({
            "type": "history", "messages": history[offset:offset + 100],
        }))
        await asyncio.sleep(0)
    await websocket.send_text(json.dumps({"type": "history_complete"}))

    # Send status
    await broadcast_status()

    try:
        while True:
            raw = await websocket.receive_text()
            event = json.loads(raw)

            if isinstance(event, dict) and event.get("type") == "ping":
                # Answered before the deployment gate, so a read-only room still proves the connection is
                # alive. Only this socket gets the reply: nothing is stored, broadcast, routed, woken or
                # counted. An id that isn't a short plain token gets no reply, so it can't be echoed.
                ping_id = event.get("id")
                if isinstance(ping_id, str) and _PING_ID.fullmatch(ping_id):
                    await websocket.send_text(json.dumps({"type": "pong", "id": ping_id}))
                continue

            gate = getattr(_runtime(), 'deployment', None)
            if gate and not gate.enter_mutation():
                await websocket.send_text(json.dumps({'type': 'error', 'text': 'Deploying: the room is temporarily read-only.'}))
                continue
            try:
                if event.get("type") == "message":
                    text = event.get("text", "").strip()
                    attachments = event.get("attachments", [])
                    sender = event.get("sender") or room_settings.get("username", "user")
                    channel = event.get("channel", "general")

                    if not text and not attachments:
                        continue
                    refusal = _stale_project(event) or _channel_refusal(channel)
                    if refusal:
                        await websocket.send_text(json.dumps({"type": "error", "text": refusal}))
                        continue

                    # Command handling
                    if text.startswith("/"):
                        cmd_parts = text.split()
                        cmd = cmd_parts[0].lower()
                        if cmd == "/clear":
                            store.clear(channel=channel)
                            await broadcast_clear(channel=channel)
                            continue

                    # Store message — the on_message callback handles broadcast + triggers
                    reply_to = event.get("reply_to")
                    if reply_to is not None:
                        reply_to = int(reply_to)

                    store.add(sender, text, attachments=attachments, reply_to=reply_to,
                              channel=channel)

                elif event.get("type") == "delete":
                    ids = event.get("ids", [])
                    # Only the active project's own chats can lose messages; legacy history is read-only.
                    problem = _stale_project(event)
                    if not problem:
                        found = [store.get_by_id(int(i)) for i in ids]
                        if any(m is None or _channel_refusal(m.get("channel", "general")) for m in found):
                            problem = "Only messages in the current project's chats can be deleted."
                    if problem:
                        await websocket.send_text(json.dumps({"type": "error", "text": problem}))
                        continue
                    if ids:
                        deleted = store.delete([int(i) for i in ids])
                        if deleted:
                            data = json.dumps({"type": "delete", "ids": deleted})
                            await _broadcast(data)
                    continue

                elif event.get("type") == "update_settings":
                    new = event.get("data", {})
                    if "title" in new and isinstance(new["title"], str):
                        room_settings["title"] = new["title"].strip() or PRODUCT_TITLE
                    apply_room_subtitle(new)
                    if "username" in new and isinstance(new["username"], str):
                        room_settings["username"] = new["username"].strip() or "user"
                    if "font" in new and new["font"] in ("mono", "serif", "sans"):
                        room_settings["font"] = new["font"]
                    if "max_agent_hops" in new:
                        try:
                            hops = int(new["max_agent_hops"])
                            hops = max(1, min(hops, 50))
                            room_settings["max_agent_hops"] = hops
                            router.max_hops = hops
                        except (ValueError, TypeError):
                            pass
                    if "contrast" in new and new["contrast"] in ("normal", "high"):
                        room_settings["contrast"] = new["contrast"]
                    if "history_limit" in new:
                        val = str(new["history_limit"]).strip().lower()
                        if val == "all":
                            room_settings["history_limit"] = "all"
                        else:
                            try:
                                val_int = int(val)
                                room_settings["history_limit"] = max(1, min(val_int, 10000))
                            except (ValueError, TypeError):
                                pass
                    _save_settings()
                    await broadcast_settings()

                elif event.get("type") == "rename_agent":
                    agent_name = (event.get("name") or "").strip()
                    new_label = (event.get("label") or "").strip()
                    managed_runtime = getattr(agents, 'runtime', None)
                    if managed_runtime and managed_runtime.handles(agent_name):
                        try:
                            await managed_runtime.change_profile({'name': new_label}, agent_name)
                        except ValueError as exc:
                            await websocket.send_text(json.dumps({'type': 'error', 'text': str(exc)}))
                        continue
                    if agent_name and new_label and registry:
                        # Derive a sanitized sender ID from the label
                        import re as _re
                        new_id = _re.sub(r'[^a-z0-9-]', '', new_label.lower().replace(' ', '-')).strip('-')
                        if not new_id:
                            new_id = agent_name  # fallback: keep old name, just change label
                        if new_id == agent_name:
                            # Same ID — label-only change
                            registry.set_label(agent_name, new_label)
                        else:
                            result = registry.rename(agent_name, new_id, new_label)
                            if isinstance(result, str):
                                # Rename failed (collision etc.) — fall back to label-only
                                registry.set_label(agent_name, new_label)
                            else:
                                # Migrate presence + cursors to new name
                                import mcp_bridge
                                mcp_bridge.migrate_identity(agent_name, new_id)
                                # Update sender on all historical messages
                                store.rename_sender(agent_name, new_id)
                                _migrate_agent_last_channel(agent_name, new_id)
                                # Notify clients so they can update sender in DOM
                                rename_event = json.dumps({
                                    "type": "agent_renamed",
                                    "old_name": agent_name,
                                    "new_name": new_id,
                                })
                                await _broadcast(rename_event)
                    continue

                elif event.get("type") == "name_pending":
                    # Human names a pending instance (from lightbox)
                    agent_name = (event.get("name") or "").strip()
                    new_label = (event.get("label") or "").strip()
                    if agent_name and registry:
                        if not new_label:
                            # Accept default name
                            registry.confirm_pending(agent_name)
                        else:
                            import re as _re
                            new_id = _re.sub(r'[^a-z0-9-]', '', new_label.lower().replace(' ', '-')).strip('-')
                            if not new_id:
                                new_id = agent_name
                            if new_id == agent_name:
                                # Same ID — just update label and confirm
                                registry.set_label(agent_name, new_label)
                                registry.confirm_pending(agent_name)
                            else:
                                result = registry.rename(agent_name, new_id, new_label)
                                if isinstance(result, str):
                                    # Rename failed — just confirm with label
                                    registry.set_label(agent_name, new_label)
                                    registry.confirm_pending(agent_name)
                                else:
                                    # Rename succeeded — confirm new name
                                    registry.confirm_pending(new_id)
                                    import mcp_bridge
                                    mcp_bridge.migrate_identity(agent_name, new_id)
                                    # Update sender on all historical messages
                                    store.rename_sender(agent_name, new_id)
                                    _migrate_agent_last_channel(agent_name, new_id)
                                    rename_event = json.dumps({
                                        "type": "agent_renamed",
                                        "old_name": agent_name,
                                        "new_name": new_id,
                                    })
                                    await _broadcast(rename_event)
                    continue

                elif event.get("type") in ("channel_create", "channel_rename", "channel_delete"):
                    # Chats are project sessions: a stable id (the store's channel key) with a
                    # project-local title. Renaming changes only the title, so history, agent
                    # sessions, cursors and board records keep pointing at the same id.
                    if not _runtime():
                        continue
                    import project_registry
                    problem = _stale_project(event)
                    try:
                        if problem:
                            raise ValueError(problem)
                        if event["type"] == "channel_create":
                            session = project_registry.create_session(_runtime(), event.get("title") or event.get("name"))
                            _save_settings()
                            await broadcast_settings()
                            await broadcast_session_created(session, event.get("ref"))
                        elif event["type"] == "channel_rename":
                            project_registry.rename_session(_runtime(), event.get("id") or event.get("old_name"),
                                                            event.get("title") or event.get("new_name"))
                            await broadcast_settings()
                        else:
                            raise ValueError("Chats cannot be deleted yet; their history stays with the project.")
                    except ValueError as exc:
                        await websocket.send_text(json.dumps({"type": "error", "text": str(exc)}))
            finally:
                if gate:
                    gate.leave_mutation()

    except WebSocketDisconnect:
        ws_clients.discard(websocket)
    except Exception:
        ws_clients.discard(websocket)
        log.exception("WebSocket error")


# --- REST endpoints ---

ALLOWED_UPLOAD_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB default


@app.post("/api/upload")
async def upload_image(file: UploadFile = File(...)):
    upload_dir = Path(config.get("images", {}).get("upload_dir", "./uploads"))
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix or ".png"
    if ext.lower() not in ALLOWED_UPLOAD_EXTS:
        return JSONResponse({"error": f"unsupported file type: {ext}"}, status_code=400)

    max_bytes = int(config.get("images", {}).get("max_size_mb", MAX_UPLOAD_BYTES // (1024 * 1024)) * 1024 * 1024)
    content = await file.read(max(0, max_bytes) + 1)
    if len(content) > max_bytes:
        return JSONResponse({"error": f"file too large (max {max_bytes // 1024 // 1024} MB)"}, status_code=400)

    filename = f"{uuid.uuid4().hex[:8]}{ext}"
    filepath = upload_dir / filename
    filepath.write_bytes(content)

    return JSONResponse({
        "name": file.filename,
        "url": f"/uploads/{filename}",
    })


# --- Export / Import ---

@app.get("/api/export")
async def export_history():
    """Download a zip archive of project history."""
    import archive as _archive
    import time as _time
    try:
        zip_bytes = await asyncio.to_thread(
            _archive.build_export, store,
            app_version=config.get("server", {}).get("version", ""),
        )
    except _archive.ExportBusyError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except Exception as exc:
        return JSONResponse({"error": f"export failed: {exc}"}, status_code=500)
    filename = f"zerocollab-export-{_time.strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/import")
async def import_history(file: UploadFile = File(...)):
    """Upload a zip archive and merge it into current stores."""
    import archive as _archive
    if not file.filename or not file.filename.lower().endswith(".zip"):
        return JSONResponse({"error": "unsupported file type: expected .zip"}, status_code=400)
    content = await file.read(_archive.MAX_IMPORT_SIZE + 1)
    if len(content) > _archive.MAX_IMPORT_SIZE:
        return JSONResponse(
            {"error": f"file too large (max {_archive.MAX_IMPORT_SIZE // 1024 // 1024}MB)"},
            status_code=400,
        )
    async with _archive.prepared_import(content) as prepared:
        # Read live settings only after preparation; no await until store and
        # channel changes have both been applied.
        channel_list = list(room_settings.get("channels", ["general"]))
        max_ch = room_settings.get("max_channels", 8)
        report = _archive.apply_import(prepared, store, channel_list, max_channels=max_ch)
        if not report.get("ok"):
            error = report.get("error", "import failed")
            status = 409 if "already running" in error else 400
            return JSONResponse({"error": error}, status_code=status)
        if report["channels"]["created"]:
            room_settings["channels"] = channel_list
            _save_settings()
    if report["channels"]["created"]:
        await broadcast_settings()
    # Tell all connected clients to reload (picks up imported messages)
    data = json.dumps({"type": "reload"})
    await _broadcast(data)
    return JSONResponse(report)


@app.get("/api/messages")
async def get_messages(request: Request, since_id: int = 0, limit: int = 50, channel: str = ""):
    ch = channel if channel else None
    # The browser reads any chat, including legacy history; token-authenticated agents
    # read only the active project's chats.
    agent = _resolve_authenticated_agent(request)
    if agent and ch and _channel_refusal(ch):
        return JSONResponse({"error": _channel_refusal(ch)}, status_code=403)
    msgs = store.get_since(since_id, channel=ch) if since_id else store.get_recent(limit, channel=ch)
    if agent and not ch and _runtime():
        msgs = [m for m in msgs if _runtime().owns_channel(m.get("channel", "general"))]
    return msgs


@app.post("/api/send")
async def api_send(request: Request):
    """REST endpoint for API agents to send messages without WebSocket.

    Authenticated via Bearer registration token. Sender is resolved from
    the token — the agent cannot impersonate another identity.
    """
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return JSONResponse({"error": "missing Authorization: Bearer <token>"}, status_code=401)
    token = auth[7:].strip()
    inst = registry.resolve_token(token) if registry else None
    if not inst:
        return JSONResponse({"error": "invalid or expired token"}, status_code=403)

    sender = inst["name"]
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)
    channel = body.get("channel") or (_runtime().default_channel() if _runtime() else "general")
    refusal = _channel_refusal(channel)
    if refusal:
        return JSONResponse({"error": refusal}, status_code=403)

    msg = store.add(sender, text, channel=channel)
    return JSONResponse(msg)


@app.get("/api/status")
async def get_status():
    status = agents.get_status()
    status["paused"] = any(router.is_paused(ch) for ch in room_settings.get("channels", ["general"]))
    return status


@app.get("/api/settings")
async def get_settings():
    return settings_payload()


@app.post("/api/register")
async def register_agent(request: Request):
    """Wrapper calls this to register a new agent instance."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    base = body.get("base", "")
    label = body.get("label")
    if not base:
        return JSONResponse({"error": "base is required"}, status_code=400)
    result = registry.register(base, label)
    if result is None:
        return JSONResponse({"error": f"unknown base: {base}"}, status_code=400)
    # Touch presence so the instance doesn't immediately time out
    import mcp_bridge
    with mcp_bridge._presence_lock:
        mcp_bridge._presence[result["name"]] = __import__("time").time()
    # If slot 1 was renamed (e.g. "claude" → "claude-1"), migrate state
    renamed = result.pop("_renamed_slot1", None)
    if renamed:
        mcp_bridge.migrate_identity(renamed["old"], renamed["new"])
        store.rename_sender(renamed["old"], renamed["new"])
        _migrate_agent_last_channel(renamed["old"], renamed["new"])
        if _event_loop:
            rename_event = json.dumps({
                "type": "agent_renamed",
                "old_name": renamed["old"],
                "new_name": renamed["new"],
            })
            asyncio.run_coroutine_threadsafe(_broadcast(rename_event), _event_loop)
    # Broadcast pending_instance event so UI can show naming lightbox
    if result.get("state") == "pending" and _event_loop:
        pending_event = json.dumps({
            "type": "pending_instance",
            "name": result["name"],
            "base": base,
            "label": result.get("label", result["name"]),
            "color": result.get("color", "#888"),
        })
        asyncio.run_coroutine_threadsafe(_broadcast(pending_event), _event_loop)
    return JSONResponse(result)


@app.post("/api/deregister/{name}")
async def deregister_agent(name: str, request: Request):
    """Wrapper calls this on shutdown to remove its instance."""
    auth_inst = _resolve_authenticated_agent(request)
    presented_token = _extract_agent_token(request)
    if presented_token and not auth_inst:
        return JSONResponse({"error": "stale_session"}, status_code=409)
    if auth_inst:
        name = auth_inst["name"]
    elif registry and registry.is_agent_family(name):
        return JSONResponse({"error": "authenticated agent session required"}, status_code=403)

    result = registry.deregister(name)
    if result is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    # Clean up runtime state (presence, activity, cursors, rename chains)
    import mcp_bridge
    mcp_bridge.purge_identity(name)
    registry.clean_renames_for(name)
    # If the remaining instance was renamed back (e.g. "claude-1" → "claude"), migrate state
    renamed = result.pop("_renamed_back", None)
    if renamed:
        mcp_bridge.migrate_identity(renamed["old"], renamed["new"])
        store.rename_sender(renamed["old"], renamed["new"])
        _migrate_agent_last_channel(renamed["old"], renamed["new"])
        if _event_loop:
            rename_event = json.dumps({
                "type": "agent_renamed",
                "old_name": renamed["old"],
                "new_name": renamed["new"],
            })
            asyncio.run_coroutine_threadsafe(_broadcast(rename_event), _event_loop)
    return JSONResponse({"ok": True})


@app.post("/api/label/{name}")
async def rename_agent_label(name: str, request: Request):
    """Rename an agent (human-initiated from UI). Changes identity + label."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    label = body.get("label", "").strip()
    if not label:
        return JSONResponse({"error": "label is required"}, status_code=400)
    managed_runtime = getattr(agents, 'runtime', None)
    if managed_runtime and managed_runtime.handles(name):
        try:
            await managed_runtime.change_profile({'name': label}, name)
            return JSONResponse({'ok': True, 'name': name})
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, 400)

    import re as _re
    new_id = _re.sub(r'[^a-z0-9-]', '', label.lower().replace(' ', '-')).strip('-')
    if not new_id:
        new_id = name

    if new_id == name:
        # Same ID — label-only change
        if registry.set_label(name, label):
            return JSONResponse({"ok": True})
        return JSONResponse({"error": "not found"}, status_code=404)

    result = registry.rename(name, new_id, label)
    if isinstance(result, str):
        # Rename failed — try label-only as fallback
        if registry.set_label(name, label):
            return JSONResponse({"ok": True, "warning": result})
        return JSONResponse({"error": result}, status_code=400)

    import mcp_bridge
    mcp_bridge.migrate_identity(name, new_id)
    # Update sender on all historical messages
    store.rename_sender(name, new_id)
    _migrate_agent_last_channel(name, new_id)
    return JSONResponse({"ok": True, "new_name": new_id})


@app.post("/api/heartbeat/{agent_name}")
async def heartbeat(agent_name: str, request: Request):
    """Wrapper calls this to keep presence alive and report activity.

    Returns the canonical name from the registry so the wrapper can
    detect renames (e.g. claim renamed 'claude-2' to 'claude-music').
    """
    import mcp_bridge
    auth_inst = _resolve_authenticated_agent(request)
    presented_token = _extract_agent_token(request)
    if presented_token and not auth_inst:
        return JSONResponse({"error": "stale_session"}, status_code=409)
    if registry and registry.is_agent_family(agent_name) and not auth_inst:
        return JSONResponse({"error": "authenticated agent session required"}, status_code=403)

    current_name = auth_inst["name"] if auth_inst else agent_name
    with mcp_bridge._presence_lock:
        mcp_bridge._presence[current_name] = __import__("time").time()
    # Optional activity report from wrapper's terminal monitor
    _activity_changed = False
    try:
        body = await request.json()
        if "active" in body:
            active_val = bool(body["active"])
            was_active = mcp_bridge._activity.get(current_name, False)
            mcp_bridge.set_active(current_name, active_val)
            _activity_changed = was_active != active_val
    except Exception:
        pass  # No body = plain heartbeat
    # Immediately broadcast on activity state change (don't wait for background checker)
    if _activity_changed:
        await broadcast_status()
    # Return canonical name so wrapper can track renames
    resp = {"ok": True, "name": current_name}
    if registry:
        # Follow rename chain (e.g. claude-2 was renamed to claude-music)
        canonical = registry.resolve_name(current_name)
        inst = registry.get_instance(canonical)
        # If rename chain didn't help, try family-based lookup
        # (handles case where _renames was cleared by server restart but
        # the instance was claimed/renamed via MCP)
        if not inst:
            base = current_name.split("-")[0] if "-" in current_name else current_name
            family_inst = registry.get_family_instance(base)
            if family_inst:
                inst = family_inst
                canonical = inst["name"]
        if inst:
            resp["name"] = inst["name"]
            resp["pending"] = inst.get("state") == "pending"
            # Also update presence under the canonical name
            if canonical != current_name:
                now = __import__("time").time()
                with mcp_bridge._presence_lock:
                    mcp_bridge._presence[canonical] = now
    return resp


# --- Open agent session in terminal ---

@app.get("/api/platform")
async def get_platform():
    """Return the server's platform so the web UI can match path formats."""
    import sys
    return JSONResponse({"platform": sys.platform})


@app.post("/api/open-path")
async def open_path(body: dict):
    """Open a file or directory in the native file manager.

    Cross-platform: Explorer on Windows, Finder on macOS, xdg-open on Linux.

    Security note: This endpoint is intended for local-only use (127.0.0.1).
    Do not expose this server on a public network without additional access controls.
    """
    import subprocess
    import sys

    path = body.get("path", "")
    if not path:
        return JSONResponse({"error": "no path"}, status_code=400)

    p = Path(path)
    try:
        if sys.platform == "win32":
            if p.is_file():
                subprocess.Popen(["explorer", "/select,", str(p)])
            elif p.is_dir():
                subprocess.Popen(["explorer", str(p)])
            else:
                return JSONResponse({"error": "path not found"}, status_code=404)
        elif sys.platform == "darwin":
            if p.is_file():
                subprocess.Popen(["open", "-R", str(p)])
            elif p.is_dir():
                subprocess.Popen(["open", str(p)])
            else:
                return JSONResponse({"error": "path not found"}, status_code=404)
        else:
            # Linux — xdg-open opens the containing folder for files
            if p.is_file():
                subprocess.Popen(["xdg-open", str(p.parent)])
            elif p.is_dir():
                subprocess.Popen(["xdg-open", str(p)])
            else:
                return JSONResponse({"error": "path not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return JSONResponse({"ok": True})


def _file_view_roots(sender: str):
    """Where a chat file reference may point: the sender's worktrees, the project repositories, other worktrees."""
    import file_preview
    import project_registry
    runtime = _runtime()
    if not runtime:
        return []
    return file_preview.candidate_roots(sender, runtime.states, project_registry.repositories(runtime))


def _resolve_file_ref(ref: str, sender: str, root):
    import file_preview
    found = file_preview.resolve(ref, _file_view_roots(sender), None if root in (None, '') else int(root))
    return found


@app.get("/api/file-view")
async def file_view(ref: str, sender: str = "", message_time: float = 0, at: str = "", root: str = ""):
    """A file referenced in chat, read-only and size-bounded, with the Git context it came from."""
    import file_preview

    def work():
        found = _resolve_file_ref(ref, sender, root)
        return file_preview.view(found, message_time or None, at or None) if found["kind"] == "ok" else found
    try:
        return JSONResponse(await asyncio.to_thread(work))
    except (file_preview.FileViewError, ValueError) as exc:
        return JSONResponse({"kind": "error", "error": str(exc)}, status_code=400)


@app.post("/api/file-view/open")
async def file_view_open(body: dict):
    """Open a chat file reference in VS Code at its line, or reveal it in the file manager.
    The server resolves the reference again; it never opens a path the browser supplies directly."""
    import file_preview

    def work():
        found = _resolve_file_ref(str(body.get("ref", "")), str(body.get("sender", "")), body.get("root"))
        if found["kind"] != "ok":
            raise file_preview.FileViewError("The file could not be found")
        return file_preview.open_externally(found, body.get("target"), body.get("line") or found.get("start"))
    try:
        return JSONResponse({"ok": True, "opened_with": await asyncio.to_thread(work)})
    except (file_preview.FileViewError, ValueError, OSError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


# Serve uploaded images
UPLOAD_MEDIA_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif',
                      '.webp': 'image/webp', '.bmp': 'image/bmp', '.svg': 'image/svg+xml',
                      # Videos agents publish with room_motion play inline in chat and on tasks (Zero #14291).
                      # Only room_motion publish writes them; /api/upload still accepts images only.
                      '.webm': 'video/webm', '.mp4': 'video/mp4'}
# Uploads come from agents and users but are served from the room's own origin, where the page holds the
# session token. An SVG opened as a page could otherwise run script with that token. The sandbox gives any
# upload opened as a document a unique origin with scripts off; <img> previews and downloads are unaffected.
UPLOAD_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
}


@app.get("/uploads/{filename}")
async def serve_upload(filename: str):
    upload_dir = Path(config.get("images", {}).get("upload_dir", "./uploads"))
    filepath = (upload_dir / filename).resolve()
    if not filepath.is_relative_to(upload_dir.resolve()):
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if not filepath.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    media_type = UPLOAD_MEDIA_TYPES.get(filepath.suffix.lower())
    if media_type is None:
        # Not an image this room accepts as an upload: never rendered, only saved.
        return FileResponse(filepath, media_type="application/octet-stream", headers=UPLOAD_HEADERS,
                            content_disposition_type="attachment", filename=filepath.name)
    return FileResponse(filepath, media_type=media_type, headers=UPLOAD_HEADERS)
