"""Entry point — starts MCP server (port 8200) + web UI (port 8300)."""

import argparse
import hashlib
import os
import asyncio
import secrets
import sys
import threading
import time
import logging
import re
from pathlib import Path

# Ensure the project directory is on the import path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


class SessionLogFilter(logging.Filter):
    def filter(self, record):
        def redact(value):
            return re.sub(r"(?i)([?&]token=)[^\s\"']+", r"\1[redacted]", value) if isinstance(value, str) else value
        record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(value) for value in record.args)
        return True


# Every same-origin /static link in index.html, with its optional query string.
# Only root-relative links: the path must start right after a quote, '(', '=' or whitespace,
# so an external URL that merely contains /static/ is left alone.
STATIC_LINK = re.compile(r"(?<=[\"'(=\s])(?P<path>/static/[^\"'\s?#)]+)(?:\?(?P<query>[^\"'\s#)]*))?")


def versioned_assets(html, build_id):
    """Stamp every /static link with ?v=<build id>: hand-written tags (zerocollab-product-1, -sweep-1,
    ...) are replaced, untagged links gain one, and any other query parameter is kept."""
    def stamp(match):
        kept = [p for p in (match['query'] or '').split('&') if p and not p.startswith('v=')]
        return match['path'] + '?' + '&'.join(kept + [f'v={build_id}'])
    return STATIC_LINK.sub(stamp, html)


def static_build_id(static_dir):
    """A short fingerprint of the static files, taken once at startup. Installing a release restarts
    the room, so each release gets new asset URLs and a browser can't keep running an old copy."""
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(static_dir).rglob('*') if p.is_file()):
        digest.update(path.relative_to(static_dir).as_posix().encode() + b'\0')
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def revalidated_static_files():
    """StaticFiles that tell the browser to check back (ETag / Last-Modified, usually a 304) before
    reusing a stored copy, instead of serving it from cache by heuristic freshness."""
    from fastapi.staticfiles import StaticFiles

    class RevalidatedStaticFiles(StaticFiles):
        async def get_response(self, path, scope):
            response = await super().get_response(path, scope)
            response.headers['Cache-Control'] = 'no-cache'
            return response
    return RevalidatedStaticFiles


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Start ZeroCollab (web UI + MCP server).",
        epilog="Flags override config.toml for this invocation. The same flags "
               "are also accepted by wrapper.py and wrapper_api.py so a launcher "
               "can isolate per-project instances by passing matching values to "
               "each process.",
    )
    parser.add_argument("--data-dir",      default=None, help="Override server.data_dir (path)")
    parser.add_argument("--port",          default=None, help="Override server.port (int)")
    parser.add_argument("--mcp-http-port", default=None, help="Override mcp.http_port (int)")
    parser.add_argument("--mcp-sse-port",  default=None, help="Override mcp.sse_port (int)")
    parser.add_argument("--upload-dir",    default=None, help="Override images.upload_dir (path)")
    parser.add_argument("--allow-network", action="store_true",
                        help="Allow binding to non-localhost hosts (with confirmation).")
    return parser.parse_args()


def main():
    for name in ("uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addFilter(SessionLogFilter())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Parse flags for --help support; the actual env propagation happens via
    # the shared config_loader.apply_cli_overrides helper so run.py and the
    # wrappers use identical extraction logic.
    _parse_args()

    from config_loader import apply_cli_overrides, load_config
    apply_cli_overrides()

    config_path = ROOT / "config.toml"
    if not config_path.exists():
        print(f"Error: {config_path} not found")
        sys.exit(1)

    config = load_config(ROOT)

    # --- Security: generate a random session token (in-memory only) ---
    session_token = secrets.token_hex(32)

    # Configure the FastAPI app (creates shared store)
    from app import app, configure, set_event_loop, store as _store_ref
    configure(config, session_token=session_token)
    import app as room_app
    from room_runtime import install
    runtime = install(room_app)

    # Share stores with the MCP bridge
    from app import store, room_settings, registry, router as app_router, agents as app_agents
    import mcp_bridge
    mcp_bridge.store = store
    mcp_bridge.room_settings = room_settings
    mcp_bridge.registry = registry
    mcp_bridge.config = config
    mcp_bridge.router = app_router
    mcp_bridge.agents = app_agents

    # Enable cursor persistence across restarts
    data_dir = ROOT / config.get("server", {}).get("data_dir", "./data")
    mcp_bridge._CURSORS_FILE = data_dir / "mcp_cursors.json"
    mcp_bridge._load_cursors()

    # Start MCP servers in background threads
    http_port = config.get("mcp", {}).get("http_port", 8200)
    sse_port = config.get("mcp", {}).get("sse_port", 8201)
    mcp_bridge.mcp_http.settings.port = http_port
    mcp_bridge.mcp_sse.settings.port = sse_port

    if os.environ.get('ZC_REHEARSAL') != '1':
        threading.Thread(target=mcp_bridge.run_http_server, daemon=True).start()
        threading.Thread(target=mcp_bridge.run_sse_server, daemon=True).start()
    time.sleep(0.5)
    logging.getLogger(__name__).info("MCP streamable-http on port %d, SSE on port %d", http_port, sse_port)

    # Mount static files
    from fastapi.responses import HTMLResponse

    static_dir = ROOT / "static"
    build_id = static_build_id(static_dir)

    @app.get("/")
    async def index():
        # Read index.html fresh each request so changes take effect without restart.
        # Inject the session token into the HTML so the browser client can use it.
        # This is safe: same-origin policy prevents cross-origin pages from reading
        # the response body, so only the user's own browser tab gets the token.
        html = versioned_assets((static_dir / "index.html").read_text("utf-8"), build_id)
        injected = html.replace(
            "</head>",
            f'<script>window.__SESSION_TOKEN__="{session_token}";</script>\n</head>',
        )
        return HTMLResponse(injected, headers={"Cache-Control": "no-store"})

    app.mount("/static", revalidated_static_files()(directory=str(static_dir)), name="static")

    # Capture the event loop for the store→WebSocket bridge
    @app.on_event("startup")
    async def on_startup():
        set_event_loop(asyncio.get_running_loop())
        await runtime.start()

    @app.on_event("shutdown")
    async def on_shutdown():
        await runtime.close()

    # Run web server
    import uvicorn
    host = config.get("server", {}).get("host", "127.0.0.1")
    port = config.get("server", {}).get("port", 8300)

    # --- Security: warn if binding to a non-localhost address ---
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"\n  !! SECURITY WARNING — binding to {host} !!")
        print("  This exposes ZeroCollab to your local network.")
        print()
        print("  Risks:")
        print("  - No TLS: traffic (including session token) is plaintext")
        print("  - Anyone on your network can sniff the token and gain full access")
        print("  - With the token, anyone can @mention agents and trigger tool execution")
        print("  - If agents run with auto-approve, this means remote code execution")
        print()
        print("  Only use this on a trusted home network. Never on public/shared WiFi.")
        if "--allow-network" not in sys.argv:
            print("  Pass --allow-network to start anyway, or set host to 127.0.0.1.\n")
            sys.exit(1)
        else:
            print()
            try:
                confirm = input("  Type YES to accept these risks and start: ").strip()
            except (EOFError, KeyboardInterrupt):
                confirm = ""
            if confirm != "YES":
                print("  Aborted.\n")
                sys.exit(1)

    print(f"\n  ZeroCollab")
    print(f"  Web UI:  http://{host}:{port}")
    print(f"  MCP HTTP: http://{host}:{http_port}/mcp  (Claude, Codex)")
    print(f"  MCP SSE:  http://{host}:{sse_port}/sse   (Gemini)")
    print(f"  Data:    {data_dir}")
    print(f"  Agents auto-trigger on @mention")
    print("\n  Browser controls carry the local session token; it is not printed.\n")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

