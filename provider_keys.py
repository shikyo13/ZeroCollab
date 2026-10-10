"""API keys for OpenAI-compatible servers (Docs/providers.md, slice S5a).

A key lives only in the operating system's credential store, in one entry per connection that also
holds the origin (scheme, host, port) it was saved for. providers.json keeps only the connection's
settings: `key_target`, the last four characters, the on/off switch and per-model prices. Every
request that carries a key checks the connection's current address against the stored origin, so an
edited providers.json can never send a key elsewhere. Keyed calls never follow redirects and never
use environment proxies (`follow_redirects=False`, `trust_env=False`).

Token usage of each keyed request is appended to data/api_usage.jsonl (no content) and summarised as
estimated cost. Agents run as the user, so this keeps keys out of agent environments, chat, files and
logs in normal work; it is not a boundary against a process that sets out to read the store."""
import base64
import ctypes
import datetime
import hashlib
import json
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit

# Printable ASCII without whitespace, double quote or backslash: every vendor key format fits. The
# stored blob is base64 in any case, so no key character reaches a command line.
KEY = re.compile(r'[!#-\[\]-~]{8,512}')
SERVICE = 'ZeroCollab'
RUNTIME = None  # set by install(); the switch state lives in the provider store, in memory


class KeyProblem(ValueError):
    """A key could not be used; the message is safe to show and never contains the key."""


def origin(url):
    parts = urlsplit(url)
    port = parts.port or {'http': 80, 'https': 443}.get(parts.scheme)
    return f'{parts.scheme}://{(parts.hostname or "").lower()}:{port}'


def host(url):
    return urlsplit(url).hostname or url


def loopback(url):
    return host(url) in ('127.0.0.1', 'localhost', '::1')


def check_key(key):
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise ValueError('Paste the API key exactly: 8 to 512 characters, with no spaces, quotes or line breaks')
    return key


# ----- credential store ---------------------------------------------------------------------------

class _Windows:
    """Windows Credential Manager through advapi32 (generic credentials, this user only)."""
    GENERIC, PERSIST_LOCAL = 1, 2
    NOT_FOUND = 1168

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [('Flags', ctypes.c_uint32), ('Type', ctypes.c_uint32), ('TargetName', ctypes.c_wchar_p),
                    ('Comment', ctypes.c_wchar_p), ('LastWritten', ctypes.c_uint64),
                    ('CredentialBlobSize', ctypes.c_uint32), ('CredentialBlob', ctypes.POINTER(ctypes.c_char)),
                    ('Persist', ctypes.c_uint32), ('AttributeCount', ctypes.c_uint32), ('Attributes', ctypes.c_void_p),
                    ('TargetAlias', ctypes.c_wchar_p), ('UserName', ctypes.c_wchar_p)]

    def __init__(self):
        self.api = ctypes.WinDLL('advapi32', use_last_error=True)
        self.api.CredWriteW.argtypes = [ctypes.POINTER(self.CREDENTIAL), ctypes.c_uint32]
        self.api.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                       ctypes.POINTER(ctypes.POINTER(self.CREDENTIAL))]
        self.api.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        self.api.CredFree.argtypes = [ctypes.c_void_p]

    def write(self, target, secret):
        blob = secret.encode('utf-8')
        buffer = ctypes.create_string_buffer(blob, len(blob))
        cred = self.CREDENTIAL(Type=self.GENERIC, TargetName=target, Comment='ZeroCollab API key',
                               CredentialBlobSize=len(blob), CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)),
                               Persist=self.PERSIST_LOCAL, UserName='zerocollab')
        if not self.api.CredWriteW(ctypes.byref(cred), 0):
            raise OSError(f'Windows Credential Manager refused the key (error {ctypes.get_last_error()})')

    def read(self, target):
        pointer = ctypes.POINTER(self.CREDENTIAL)()
        if not self.api.CredReadW(target, self.GENERIC, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == self.NOT_FOUND:
                return None
            raise OSError(f'Windows Credential Manager could not read the key (error {ctypes.get_last_error()})')
        try:
            cred = pointer.contents
            return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize).decode('utf-8')
        finally:
            self.api.CredFree(pointer)

    def delete(self, target):
        if not self.api.CredDeleteW(target, self.GENERIC, 0) and ctypes.get_last_error() != self.NOT_FOUND:
            raise OSError(f'Windows Credential Manager could not delete the key (error {ctypes.get_last_error()})')


class _MacOS:
    """Keychain through `security`. The secret goes through stdin (`security -i`), never argv."""

    def run(self, command, check=True):
        result = subprocess.run(['security', '-i'], input=command + '\n', capture_output=True, text=True, timeout=15)
        if check and result.returncode:
            raise OSError('Keychain refused the request')
        return result

    def write(self, target, secret):
        # secret is base64 (see save), so it needs no quoting on the `security -i` command line.
        self.run(f'add-generic-password -U -s {SERVICE} -a {target} -w {secret}')

    def read(self, target):
        result = subprocess.run(['security', 'find-generic-password', '-s', SERVICE, '-a', target, '-w'],
                                capture_output=True, text=True, timeout=15)
        return result.stdout.rstrip('\n') if result.returncode == 0 else None

    def delete(self, target):
        subprocess.run(['security', 'delete-generic-password', '-s', SERVICE, '-a', target],
                       capture_output=True, text=True, timeout=15)


class _Linux:
    """Secret Service through `secret-tool`; the secret goes through stdin."""

    def write(self, target, secret):
        result = subprocess.run(['secret-tool', 'store', '--label', f'{SERVICE} {target}', 'service', SERVICE, 'target', target],
                                input=secret, capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise OSError('The Secret Service refused the key; is a keyring running?')

    def read(self, target):
        result = subprocess.run(['secret-tool', 'lookup', 'service', SERVICE, 'target', target],
                                capture_output=True, text=True, timeout=15)
        return result.stdout if result.returncode == 0 and result.stdout else None

    def delete(self, target):
        subprocess.run(['secret-tool', 'clear', 'service', SERVICE, 'target', target], capture_output=True, text=True, timeout=15)


_STORE = None


def store():
    global _STORE
    if _STORE is None:
        try:
            _STORE = _Windows() if sys.platform == 'win32' else _MacOS() if sys.platform == 'darwin' else _Linux()
        except OSError as exc:
            raise KeyProblem(f'The credential store is not available ({exc})') from exc
    return _STORE


def target(data, connection):
    """One entry per connection and room data folder, so removing a connection deletes exactly one."""
    room = hashlib.sha256(str(data).encode()).hexdigest()[:8]
    return f'{SERVICE}/provider/{connection}@{room}'


def save(data, connection, key, url):
    check_key(key)
    wanted = {'key': key, 'origin': origin(url)}
    blob = base64.b64encode(json.dumps(wanted).encode()).decode()
    try:
        previous = store().read(target(data, connection))  # a replacement must leave this in place on failure
    except (OSError, subprocess.SubprocessError):
        previous = None
    try:
        store().write(target(data, connection), blob)
    except (OSError, subprocess.SubprocessError) as exc:
        raise KeyProblem(f'Could not save the key: {exc}') from exc
    # Fail closed on a store that reports success without writing, or can't be read back (security
    # a175d747, 0250a430): restore the previous entry, or delete the new one if there was none.
    try:
        kept = load(data, connection) == wanted
    except KeyProblem:
        kept = False
    if not kept:
        try:
            store().write(target(data, connection), previous) if previous else delete(data, connection)
        except (OSError, subprocess.SubprocessError):
            delete(data, connection)
        raise KeyProblem('The credential store did not keep the key. Nothing was saved.')


def delete(data, connection):
    try:
        store().delete(target(data, connection))
    except (OSError, subprocess.SubprocessError, KeyProblem):
        pass  # Nothing to delete is fine; a store that is gone holds no key.


def load(data, connection):
    try:
        raw = store().read(target(data, connection))
        stored = json.loads(base64.b64decode(raw.strip(), validate=True)) if raw else None
    except (OSError, subprocess.SubprocessError, ValueError) as exc:  # binascii.Error is a ValueError
        raise KeyProblem(f'Could not read the saved key ({type(exc).__name__})') from exc
    if not isinstance(stored, dict) or not isinstance(stored.get('key'), str) or not isinstance(stored.get('origin'), str):
        return None
    return stored


# ----- request headers ----------------------------------------------------------------------------

def bearer(key):
    return {'Authorization': 'Bearer ' + key}


def headers(cfg, label=None):
    """Headers for one request to cfg['base_url']: {} for a connection without a key. Raises with a
    message to show when the key is missing, switched off or saved for another origin."""
    connection = cfg.get('key_target') if isinstance(cfg, dict) else None
    if not connection:
        return {}
    # An agent's config carries the agent's name as its label; messages name the connection.
    templates = RUNTIME.profiles.templates if RUNTIME else {}
    name = label or templates.get(connection, {}).get('label') or connection
    entry = RUNTIME.profiles.store.entries.get(connection, {}) if RUNTIME else {}
    if entry.get('enabled') is False:
        raise KeyProblem(f'API provider turned off: {name}. Turn it on in Team > Providers.')
    stored = load(RUNTIME.data, connection) if RUNTIME else None
    if not stored:
        raise KeyProblem(f'The API key for {name} is missing. Enter it again in Team > Providers.')
    if stored['origin'] != origin(cfg.get('base_url', '')):
        raise KeyProblem(f'The address for {name} changed outside the app; no key was sent. Enter its API key again in Team > Providers.')
    return bearer(stored['key'])


def failure(exc, url):
    """A safe message for a failed keyed request: never the server's reply or the key. The caller
    says what happened to the key (not saved, or the previous key kept)."""
    import httpx
    where = host(url)
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
        return f'Key not accepted by {where}.'
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return f"Couldn't reach {where}."
    if isinstance(exc, httpx.HTTPStatusError):
        return f'{where} answered HTTP {exc.response.status_code}.'
    return f'{where} did not answer as an OpenAI-compatible server ({type(exc).__name__}).'


# ----- usage and estimated cost -------------------------------------------------------------------

def usage_path():
    return RUNTIME.data / 'api_usage.jsonl'


def record(cfg, agent, run_id, model, usage):
    """Append one keyed response's token usage (no content). usage None means the server reported none."""
    connection = cfg.get('key_target') if isinstance(cfg, dict) else None
    if not connection or RUNTIME is None:
        return
    row = {'connection': connection, 'agent': agent, 'run': run_id, 'model': model, 'time': time.time()}
    def count(value):
        # Servers vary: a missing count is 0, but anything that isn't a number makes this usage unknown.
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError
        return max(0, int(value))

    try:
        details = usage.get('prompt_tokens_details') or {}
        prompt, cached = count(usage.get('prompt_tokens')), count(details.get('cached_tokens') if isinstance(details, dict) else None)
        row.update(input=max(0, prompt - cached), cached=cached, output=count(usage.get('completion_tokens')))
    except (AttributeError, ValueError, OverflowError):  # no usage, or usage the room can't read (Infinity included): never fail the turn over it
        row['missing'] = True
    with usage_path().open('a', encoding='utf-8') as f:
        f.write(json.dumps(row) + '\n')


def price_of(prices, model):
    """Per-million prices for a model, or None when the input or output price is unknown.
    A blank cached-input price is charged at the input price, so the estimate errs high."""
    price = (prices or {}).get(model) or {}
    if not isinstance(price.get('input'), (int, float)) or not isinstance(price.get('output'), (int, float)):
        return None
    cached = price.get('cached') if isinstance(price.get('cached'), (int, float)) else price['input']
    return price['input'], cached, price['output']


def summary(connection, prices, now=None):
    """Today, this month and per agent for one connection. A total that includes a response with no
    usage or no price is marked incomplete; missing parts are never counted as zero."""
    now = datetime.datetime.fromtimestamp(now or time.time())
    day, month = now.date(), (now.year, now.month)

    def bucket():
        return {'cost': 0.0, 'complete': True, 'input': 0, 'cached': 0, 'output': 0}

    today, this_month, agents, missing_price, missing_usage = bucket(), bucket(), {}, set(), False
    path = usage_path()
    for line in path.read_text('utf-8').splitlines() if path.exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('connection') != connection:
            continue
        when = datetime.datetime.fromtimestamp(row.get('time', 0))
        if (when.year, when.month) != month:
            continue
        targets = [this_month, agents.setdefault(row.get('agent', '?'), bucket())] + ([today] if when.date() == day else [])
        price = price_of(prices, row.get('model'))
        if row.get('missing'):
            missing_usage = True
        elif price is None:
            missing_price.add(row.get('model') or '?')
        for b in targets:
            if row.get('missing'):
                b['complete'] = False
                continue
            for key in ('input', 'cached', 'output'):
                b[key] += row.get(key, 0)
            if price is None:
                b['complete'] = False
            else:
                b['cost'] += (row['input'] * price[0] + row['cached'] * price[1] + row['output'] * price[2]) / 1_000_000
    incomplete = []
    if missing_price:
        incomplete.append('no price for ' + ', '.join(sorted(missing_price)))
    if missing_usage:
        incomplete.append('some responses reported no token usage')
    return {'today': today, 'month': this_month, 'agents': agents, 'incomplete': incomplete}


def install(host, runtime):
    global RUNTIME
    RUNTIME = runtime
