"""Browser sign-in for the web room, so the page never carries the UI token (security c6e72a34,
design Docs/security/ui-session-proposal.md r2, approved e1776689).

The browser room lives at http://zerocollab.localhost:<port>. Cookies ignore ports, so a cookie for
127.0.0.1 would reach every agent preview on 127.0.0.1; this host name is used by nothing else.
The supervisor, which holds the UI token, mints a single-use launch code and opens the browser at
/launch#code=<code>. The page exchanges the code once for an HttpOnly, SameSite=Strict cookie whose
secret is not the UI token. The room keeps only SHA-256 hashes of codes and session secrets, so no
route or process can hand a reusable secret to anyone; the secret exists only in the browser.
"""
import base64
import hashlib
import json
import re
import secrets
import time

ROOM_HOST = 'zerocollab.localhost'
COOKIE = 'zc_ui'
CODE_SECONDS = 60
USED_SECONDS = 3600          # how long a used code is remembered, for revoke on reuse
SESSION_SECONDS = 30 * 86400  # server-side cap; the cookie itself is a session cookie
HEADER_ONLY_PATHS = ('/api/runtime/launch-codes', '/api/runtime/ui-sessions')
REFUSED = 'This link was already used or expired. Open ZeroCollab again from the tray or Start-Room.'
REUSED_ALERT = ('A sign-in link was used twice, so the browser it signed in has been signed out. '
                'Open ZeroCollab again from the tray or Start-Room. If you did not open it twice, '
                'something on this computer may have taken the link.')
_HEX64 = re.compile(r'^[0-9a-f]{64}$')


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class BrowserSessions:
    """Launch codes and browser sessions, by hash only. Lookups are on SHA-256 digests of the secrets,
    so a timing difference can reveal nothing about a stored secret."""

    def __init__(self, port, hashes=(), alert=None, clock=time.time):
        self.host = f'{ROOM_HOST}:{port}'
        self.origin = f'http://{self.host}'
        self.alert = alert
        self.clock = clock
        self.codes = {}     # digest(code) -> expiry
        self.used = {}      # digest(code) -> [digest(session) or None, used at]
        self.sessions = {}  # digest(secret) -> created
        now = clock()
        for item in hashes or ():
            if (isinstance(item, dict) and isinstance(item.get('hash'), str) and _HEX64.match(item['hash'])
                    and isinstance(item.get('created'), (int, float)) and 0 <= now - item['created'] < SESSION_SECONDS):
                self.sessions[item['hash']] = float(item['created'])

    def purge(self):
        now = self.clock()
        self.codes = {h: t for h, t in self.codes.items() if t > now}
        self.used = {h: v for h, v in self.used.items() if now - v[1] < USED_SECONDS}
        self.sessions = {h: t for h, t in self.sessions.items() if now - t < SESSION_SECONDS}

    def at_room(self, host):
        return host == self.host

    def mint(self):
        """A fresh single-use code. Only the UI-token holder (the supervisor) reaches this."""
        self.purge()
        code = secrets.token_urlsafe(32)
        self.codes[digest(code)] = self.clock() + CODE_SECONDS
        return code

    def redeem(self, code):
        """The session secret for a valid unused code, else None. A second presentation of a used code
        ends the session that code created and raises the room alert."""
        self.purge()
        if not isinstance(code, str) or not 0 < len(code) <= 128:
            return None
        key = digest(code)
        if self.codes.pop(key, None) is not None:
            secret = secrets.token_urlsafe(32)
            session = digest(secret)
            self.sessions[session] = self.clock()
            self.used[key] = [session, self.clock()]
            return secret
        if key in self.used:
            session = self.used[key][0]
            self.used[key][0] = None
            if session and self.sessions.pop(session, None) is not None and self.alert:
                self.alert(REUSED_ALERT)
        return None

    def valid(self, secret):
        if not isinstance(secret, str) or not 0 < len(secret) <= 128:
            return False
        created = self.sessions.get(digest(secret))
        return created is not None and self.clock() - created < SESSION_SECONDS

    def end(self, secret):
        """Sign one session out, e.g. when the shared browser that held it closes."""
        if isinstance(secret, str):
            self.sessions.pop(digest(secret), None)

    def hand_over(self):
        """Session hashes and creation times for the next room process. Never a secret."""
        self.purge()
        return [{'hash': h, 'created': t} for h, t in self.sessions.items()]


# The launch page: one fixed inline script, pinned by hash. It removes the code from the address bar
# before doing anything else, then exchanges it and moves on to the room.
_LAUNCH_SCRIPT = (
    "var m=/[#&]code=([^&]+)/.exec(location.hash);history.replaceState(null,'','/launch');"
    "var s=document.getElementById('s');"
    "if(!m){s.textContent='This sign-in link is incomplete. Open ZeroCollab again from the tray or Start-Room.';}"
    "else{fetch('/launch',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},"
    "body:JSON.stringify({code:decodeURIComponent(m[1])})}).then(function(r){"
    "if(r.ok){location.replace('/');return;}"
    "return r.json().then(function(b){s.textContent=b.error;});"
    "}).catch(function(){s.textContent='The room did not answer. Is ZeroCollab running?';});}"
)
LAUNCH_HTML = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>ZeroCollab</title></head>'
               '<body><p id="s">Signing in to ZeroCollab...</p>'
               f'<script>{_LAUNCH_SCRIPT}</script></body></html>')
_SCRIPT_HASH = base64.b64encode(hashlib.sha256(_LAUNCH_SCRIPT.encode('utf-8')).digest()).decode()
LAUNCH_HEADERS = {
    'Cache-Control': 'no-store',
    'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff',
    'Content-Security-Policy': (f"default-src 'none'; script-src 'sha256-{_SCRIPT_HASH}'; connect-src 'self'; "
                                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
}


def read_handoff(stream):
    """The supervisor's one JSON line on stdin: {"ui_token": str, "session_hashes": [...],
    "room_writer": int, "start_stopped": "desktop"}. room_writer is the supervisor's data-folder lease,
    duplicated into this process (room_writer.py); start_stopped names the other app that used the data last,
    so the room opens Stopped. Empty when stdin is a terminal (development) or carries nothing (an older
    supervisor gives DEVNULL)."""
    if stream is None or stream.isatty():
        return {}
    try:
        line = stream.readline()
        data = json.loads(line) if line.strip() else {}
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    token = data.get('ui_token')
    hashes = data.get('session_hashes')
    writer = data.get('room_writer')
    stopped = data.get('start_stopped')
    return {'ui_token': token if isinstance(token, str) and len(token) >= 32 else None,
            'session_hashes': hashes if isinstance(hashes, list) else [],
            'room_writer': writer if isinstance(writer, int) and not isinstance(writer, bool) and writer > 0 else None,
            'start_stopped': stopped if stopped in ('desktop',) else None}
