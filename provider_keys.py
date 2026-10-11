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

import provider_prices

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


# S5b: API keys that the official CLIs use through the room relay (provider_relay.py). Each vendor
# has one fixed origin, so the connection's address can't be edited.
VENDORS = {
    'claude': {'origin': 'https://api.anthropic.com', 'label': 'Anthropic API'},
    'codex': {'origin': 'https://api.openai.com', 'label': 'OpenAI API'},
}


def auth(provider, key):
    """The vendor's own auth header: Anthropic takes x-api-key, OpenAI-style servers a bearer token."""
    return {'x-api-key': key} if provider == 'claude' else bearer(key)


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
    problem = limit_problem(name, entry)
    if problem:
        # S5c: every keyed request passes here (local turns, the relay, model lists), so none starts past the
        # limit. Requests already running complete, so the month can end above it by those responses.
        raise KeyProblem(problem)
    return auth(cfg.get('provider'), stored['key'])


def limit_problem(name, entry):
    """Why no new request may start under this connection's monthly limit, or None. Missing prices are
    never counted as zero (security d64927b2): this month's usage of a model without a price stops new
    requests until it is priced, so unpriced spend is bounded by one response per newly seen model.
    Responses that reported no usage, or no model, can't be priced and are only shown as incomplete."""
    limit = entry.get('limit')
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not limit > 0:
        return f'The monthly limit for {name} is invalid. Set it again in Team > Providers.'
    cost, unpriced = month_state(entry.get('key_target', ''), prices_for(entry))
    if unpriced:
        return (f"Monthly limit for {name} can't be checked: no price for {', '.join(unpriced)}. "
                'Add prices in Team > Providers.')
    if cost >= limit:
        return f'Monthly API limit reached for {name} (${limit:,.2f}). Raise it in Team > Providers.'
    return None


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


def count(value):
    """A token count from a server. Servers vary: a missing count is 0, but anything that isn't a
    number (Infinity included) makes the usage unknown (ValueError or OverflowError)."""
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError
    return max(0, int(value))


def append_usage(connection, agent, run_id, model, counts):
    """One keyed response's row (no content). counts: {input, cached, output[, cache_write]} with input
    excluding cached reads and cache writes, or None when the response reported no usage."""
    if not connection or RUNTIME is None:
        return
    row = {'connection': connection, 'agent': agent, 'run': run_id, 'model': model, 'time': time.time()}
    row.update(counts if counts is not None else {'missing': True})
    with usage_path().open('a', encoding='utf-8') as f:
        f.write(json.dumps(row) + '\n')


def record(cfg, agent, run_id, model, usage):
    """Append one keyed response's token usage from an OpenAI-style chat `usage`; None means none reported."""
    connection = cfg.get('key_target') if isinstance(cfg, dict) else None
    try:
        details = usage.get('prompt_tokens_details') or {}
        prompt, cached = count(usage.get('prompt_tokens')), count(details.get('cached_tokens') if isinstance(details, dict) else None)
        counts = {'input': max(0, prompt - cached), 'cached': cached, 'output': count(usage.get('completion_tokens'))}
    except (AttributeError, ValueError, OverflowError):  # no usage, or usage the room can't read: never fail the turn over it
        counts = None
    append_usage(connection, agent, run_id, model, counts)


def prices_for(entry):
    """The prices a key connection is charged at: the built-in list for its CLI (S5b: Anthropic for
    Claude, OpenAI for Codex), with the prices the user entered taking precedence per model."""
    entry = entry if isinstance(entry, dict) else {}
    return {**provider_prices.builtin(entry.get('provider')), **(entry.get('prices') or {})}


def _rates(price):
    """(input, cached, output, cache_write) per million, or None without an input and output price.
    A blank cached-input price is charged at the input price, so the estimate errs high."""
    if not isinstance(price, dict) or not isinstance(price.get('input'), (int, float)) or not isinstance(price.get('output'), (int, float)):
        return None
    cached = price.get('cached') if isinstance(price.get('cached'), (int, float)) else price['input']
    # Anthropic prompt-cache writes cost 1.25x input for the 5-minute cache (2x for 1 hour); without a
    # price of its own, the higher rate keeps the estimate on the high side.
    write = price.get('cache_write') if isinstance(price.get('cache_write'), (int, float)) else price['input'] * 2
    return price['input'], cached, price['output'], write


def price_of(prices, model):
    """A model's price entry (exact ID, or without a dated snapshot suffix), or None when its input or
    output price is unknown."""
    price = provider_prices.lookup(prices, model)
    return price if _rates(price) else None


# Service tiers billed at standard rates or less (batch and flex are cheaper, so counting them as standard
# errs high). Any other tier the vendor reports (priority, fast) is priced on its own (security f066876a).
STANDARD_TIERS = {'', 'standard', 'default', 'auto', 'batch', 'flex'}


def priced_name(row):
    """The name a usage row is priced under: its model, or '<model> (<tier>)' for a non-standard service
    tier, so a priority or fast-mode response never counts at standard rates. Without a price entered for
    that name it is unpriced, and with a limit set it stops new requests until it is priced."""
    model, tier = row.get('model') or '', str(row.get('tier') or '').lower()
    return f'{model} ({tier})' if model and tier not in STANDARD_TIERS else model


def row_cost(price, row):
    """USD for one usage row. A size tier (`long`) applies when the request's whole prompt (input, cache
    reads and cache writes) is above its threshold, as the vendors bill each request on its own."""
    prompt = row.get('input', 0) + row.get('cached', 0) + row.get('cache_write', 0)
    long = price.get('long')
    rates = _rates({**price, **long}) if isinstance(long, dict) and prompt > long.get('above', float('inf')) else _rates(price)
    return (row['input'] * rates[0] + row['cached'] * rates[1] + row['output'] * rates[2]
            + row.get('cache_write', 0) * rates[3]) / 1_000_000


_MONTH = {}  # connection -> (stamp, (cost, unpriced models))


def month_state(connection, prices):
    """This month's priced cost and the models used without a price, for one connection; cached per
    connection until the usage file, the prices or the month change, so the per-request limit check
    doesn't re-read the file."""
    path = usage_path()
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None
    stamp = (stamp, json.dumps(prices, sort_keys=True), datetime.date.today().strftime('%Y-%m'))
    cached = _MONTH.get(connection)
    if not cached or cached[0] != stamp:
        totals = summary(connection, prices)
        cached = _MONTH[connection] = (stamp, (totals['month']['cost'], totals['unpriced']))
    return cached[1]


def summary(connection, prices, now=None):
    """Today, this month and per agent for one connection. A total that includes a response with no
    usage or no price is marked incomplete; missing parts are never counted as zero."""
    now = datetime.datetime.fromtimestamp(now or time.time())
    day, month = now.date(), (now.year, now.month)

    def bucket():
        return {'cost': 0.0, 'complete': True, 'input': 0, 'cached': 0, 'output': 0}

    today, this_month, agents, missing_price, missing_usage, seen, unpriced = bucket(), bucket(), {}, set(), False, set(), set()
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
        name = priced_name(row)
        if name:
            seen.add(name)
        price = price_of(prices, name)
        if row.get('missing'):
            missing_usage = True
        elif price is None:
            missing_price.add(name or '?')
            if name:
                unpriced.add(name)
        for b in targets:
            if row.get('missing'):
                b['complete'] = False
                continue
            # Cache writes (Anthropic) are input tokens for the token columns, priced at their own rate.
            b['input'] += row.get('input', 0) + row.get('cache_write', 0)
            b['cached'] += row.get('cached', 0)
            b['output'] += row.get('output', 0)
            if price is None:
                b['complete'] = False
            else:
                b['cost'] += row_cost(price, row)
    incomplete = []
    if missing_price:
        incomplete.append('no price for ' + ', '.join(sorted(missing_price)))
    if missing_usage:
        incomplete.append('some responses reported no token usage')
    # models: what this connection served this month, so each can be priced (a Claude or Codex key has no single model).
    # unpriced: named models this month without a price; with a limit set they stop new requests (S5c).
    return {'today': today, 'month': this_month, 'agents': agents, 'incomplete': incomplete, 'models': sorted(seen),
            'unpriced': sorted(unpriced)}


def install(host, runtime):
    global RUNTIME
    RUNTIME = runtime
