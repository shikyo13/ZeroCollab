"""Provider setup for Team > Providers (Docs/providers.md, slices S1 and S2).

Status (S1) asks each official CLI for its version and sign-in status and probes local
OpenAI-compatible servers. It never reads credential files and returns no account identity
(email, organization) - only whether the CLI is signed in and how.

Sign-in (S2) opens a visible console where the user runs the provider's own official login;
ZeroCollab never sees the password or token. Run as `python provider_setup.py sign-in <provider>`
inside that console. It never signs out."""
import asyncio
import functools
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
from fastapi.responses import JSONResponse

import native_adapters as native
import provider_keys

# Default ports of the local servers users run most; all of them speak the OpenAI /v1 API.
# Apple's MLX LM server defaults to llama.cpp's port 8080; local_rows() credits that port only on evidence.
LOCAL_SERVERS = (
    ('ollama', 'Ollama', 'http://127.0.0.1:11434/v1', 'ollama serve'),
    ('lmstudio', 'LM Studio', 'http://127.0.0.1:1234/v1', 'lms server start'),
    ('llamacpp', 'llama.cpp', 'http://127.0.0.1:8080/v1', 'llama-server --jinja -m <model.gguf>'),
    ('mlx', 'MLX LM', 'http://127.0.0.1:8080/v1', 'mlx_lm.server --model <mlx-community/model>'),
    ('mlxserve', 'mlx-serve', 'http://127.0.0.1:11234/v1', 'mlx-serve serve'),
)
# MLX runs only on Apple silicon Macs, so other computers neither list nor probe these servers.
MAC_ONLY = {'mlx', 'mlxserve'}
# The official sign-in command per provider; the words after the program name are its arguments.
SIGN_IN = {'claude': 'claude auth login', 'codex': 'codex login', 'antigravity': 'agy'}
LABELS = {'claude': 'Claude', 'codex': 'Codex', 'antigravity': 'Antigravity'}
CLI_TIMEOUT = 15
# Providers whose CLI has a sign-in status command, so the page can wait for it to report signed in.
REPORTS_SIGN_IN = {'claude', 'codex'}
# Sign-in consoles this server opened, so a second click does not stack windows. Only tracked where the
# launched process lives as long as the window (Windows console, xterm); macOS Terminal and terminal
# wrappers that hand off to a server return at once, so repeated clicks there can open another window.
SIGN_IN_WINDOWS = {}
SIGN_IN_LOCK = threading.Lock()  # launches run in worker threads: check and create as one step
MODEL_LIMIT = 50


async def run_cli(args, timeout=CLI_TIMEOUT):
    """(exit code, stdout, stderr) of a short CLI query, or raises TimeoutError."""
    proc = await asyncio.create_subprocess_exec(*args, env=native.subscription_env(), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    return proc.returncode, out.decode('utf-8', 'replace').strip(), err.decode('utf-8', 'replace').strip()


def unknown(detail):
    return {'state': 'unknown', 'detail': detail[:300]}


def claude_sign_in(code, out, err):
    """Parses `claude auth status --json`, which exits non-zero when signed out but still prints JSON."""
    try:
        data = json.loads(out)
    except ValueError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get('loggedIn'), bool):
        return unknown(f'Claude did not report its sign-in status (exit {code}){": " + err if err else ""}')
    if not data['loggedIn']:
        return {'state': 'signed_out', 'detail': 'Not signed in'}
    method = data.get('authMethod') or ''
    plan = data.get('subscriptionType') or ''
    if method == 'claude.ai':
        return {'state': 'signed_in', 'billing': 'subscription', 'method': 'Claude subscription' + (f' ({plan})' if plan else '')}
    if method == 'console':
        return {'state': 'signed_in', 'billing': 'api', 'method': 'Anthropic Console (API billing)'}
    return {'state': 'signed_in', 'billing': 'unknown', 'method': f'Signed in ({method or "method not reported"})'}


def codex_sign_in(code, out, err):
    """Parses `codex login status`. Its signed-in line can include a masked API key, so signed-in
    output is never echoed; only unrecognized failures quote the CLI's first line."""
    line = next((ln.strip() for ln in (out + '\n' + err).splitlines() if ln.strip()), '')
    lower = line.lower()
    if lower.startswith('not logged in'):
        return {'state': 'signed_out', 'detail': 'Not signed in'}
    if code == 0 and lower.startswith('logged in'):
        if 'chatgpt' in lower:
            return {'state': 'signed_in', 'billing': 'subscription', 'method': 'ChatGPT subscription'}
        if 'api key' in lower:
            return {'state': 'signed_in', 'billing': 'api', 'method': 'OpenAI API key (API billing)'}
        return {'state': 'signed_in', 'billing': 'unknown', 'method': 'Signed in (method not reported)'}
    quoted = line if line and not lower.startswith('logged in') else ''
    return unknown(f'Codex did not report its sign-in status (exit {code}){": " + quoted if quoted else ""}')


async def sign_in_status(provider, executable):
    """state is signed_in, signed_out or unknown. When signed in, billing is what the CLI reports
    (subscription, api or unknown); a stored API login still bills that API account."""
    if provider == 'claude':
        return claude_sign_in(*await run_cli([*executable, 'auth', 'status', '--json']))
    if provider == 'codex':
        return codex_sign_in(*await run_cli([*executable, 'login', 'status']))
    # Antigravity and Gemini have no status command; their model-list check is the only evidence.
    return unknown(f'{provider.capitalize()} has no sign-in status command; see its model list')


async def cli_status(provider):
    try:
        executable = native.command(provider)
    except RuntimeError as exc:
        return {'installed': False, 'detail': str(exc)}
    result = {'installed': True, 'path': executable[-1]}
    try:
        code, out, err = await run_cli([*executable, '--version'])
        result['version'] = (out or err).splitlines()[0][:80] if code == 0 and (out or err) else ''
        result['sign_in'] = {**await sign_in_status(provider, executable), 'command': SIGN_IN.get(provider, ''),
                             'reports': provider in REPORTS_SIGN_IN}
    except Exception as exc:  # timeouts, missing executables, odd output: this provider's row says why
        result['sign_in'] = {'state': 'unknown', 'detail': f'{type(exc).__name__}: {exc}'[:300], 'command': SIGN_IN.get(provider, ''),
                             'reports': provider in REPORTS_SIGN_IN}
    return result


def local_installed(key):
    """Whether a local server app is installed, so a closed port can say 'start it' rather than 'install it'."""
    try:
        home = Path.home()
    except RuntimeError:  # no HOME/USERPROFILE (service accounts, stripped environments)
        home = Path('/nonexistent')
    if key == 'ollama':
        return bool(shutil.which('ollama')) or (Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Ollama/ollama.exe').is_file() \
            or Path('/Applications/Ollama.app').exists()
    if key == 'lmstudio':
        return bool(shutil.which('lms')) or (home / '.lmstudio/bin').is_dir() or Path('/Applications/LM Studio.app').exists()
    if key == 'llamacpp':
        return bool(shutil.which('llama-server'))
    if key == 'mlx':  # pip install mlx-lm; newer releases also ship the `mlx_lm` command
        return bool(shutil.which('mlx_lm.server') or shutil.which('mlx_lm'))
    if key == 'mlxserve':
        return bool(shutil.which('mlx-serve')) or Path('/Applications/MLX-Serve.app').exists()
    return False


async def probe_server(client, base_url):
    try:
        response = await client.get(base_url.rstrip('/') + '/models')
        response.raise_for_status()
        body = response.json()
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return {'reachable': False, 'detail': 'Not running'}
    except (httpx.HTTPError, ValueError) as exc:
        return {'reachable': False, 'detail': f'Not an OpenAI-compatible /models endpoint ({type(exc).__name__})'}
    except Exception as exc:  # e.g. httpx.InvalidURL from a bad base_url: fail this row, not the whole status call
        return {'reachable': False, 'detail': f'Could not check this server ({type(exc).__name__}: {exc})'[:300]}
    data = body.get('data') if isinstance(body, dict) else None
    if not isinstance(data, list):
        return {'reachable': False, 'detail': 'Not an OpenAI-compatible /models endpoint (no data list)'}
    ids = [m['id'] for m in data if isinstance(m, dict) and isinstance(m.get('id'), str) and m['id']]
    return {'reachable': True, 'models': ids[:MODEL_LIMIT], 'model_count': len(ids),
            'served_by': served_by(response, data)}


CHECK_TOOL = {'type': 'function', 'function': {
    'name': 'report_ready', 'description': 'Report that you can call tools.',
    'parameters': {'type': 'object', 'properties': {'ready': {'type': 'boolean'}}, 'required': ['ready']}}}
# A first request can load the model into memory; local servers may take a while.
CHECK_TIMEOUT = 120


MARKUP_FIX = ('Returned tool markup as text instead of a structured call. For llama.cpp start llama-server with --jinja; '
              'for Ollama or LM Studio choose a tool-capable model.')


async def tool_call_check(base_url, model, request_options=None, max_tokens=1024, headers=None):
    """Agents need structured tool calls. One tiny request with one tool, against exactly this address
    and model, sent the way a real local turn sends it (streamed, with the connection's request
    options) and accepted by the same code: room_runtime.stream_chat assembles it, and
    local_protocol.visible_text and validate_calls judge it."""
    from local_protocol import validate_calls, visible_text
    from room_runtime import stream_chat
    # The connection's own response length: reasoning models spend part of it thinking before the call.
    payload = {'model': model, 'stream': True, 'stream_options': {'include_usage': True}, 'max_tokens': max_tokens, 'temperature': 0,
               'tools': [CHECK_TOOL], 'tool_choice': 'auto',
               'messages': [{'role': 'user', 'content': 'Call the report_ready tool with ready set to true. Do not answer in text.'}]}
    payload.update(request_options or {})
    payload['model'] = model
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(CHECK_TIMEOUT, connect=3), trust_env=False, headers=headers or {}) as client:
            text, calls, finish_reason, _ = await stream_chat(client, base_url.rstrip('/'), payload)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return {'ok': False, 'detail': 'The server is not running at this address'}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403) and headers:
            return {'ok': False, 'detail': 'The server did not accept the key for a chat request'}
        return {'ok': False, 'detail': f'The server refused a streamed tool request (HTTP {exc.response.status_code})'}
    except httpx.TimeoutException:
        return {'ok': False, 'detail': f'No reply within {CHECK_TIMEOUT} seconds; the model may still be loading. Check again.'}
    except (httpx.HTTPError, ValueError) as exc:
        return {'ok': False, 'detail': f'Not an OpenAI-compatible streaming chat endpoint ({type(exc).__name__})'}
    try:
        visible_text(text)
    except ValueError:
        return {'ok': False, 'detail': MARKUP_FIX}
    if finish_reason == 'length':
        return {'ok': False, 'detail': f'Ran out of response length ({max_tokens:,} tokens) before completing the tool call; '
                                       'raise the maximum response length (reasoning models think first) and check again'}
    try:
        validated = validate_calls(list(calls.values()), [CHECK_TOOL])
    except ValueError as exc:
        return {'ok': False, 'detail': f'Returned a tool call the room would reject: {exc}'}
    if any(name == 'report_ready' for _, name, _ in validated):
        return {'ok': True, 'detail': 'Returned a structured tool call, streamed as real turns use it'}
    if 'report_ready' in text:
        return {'ok': False, 'detail': MARKUP_FIX}
    return {'ok': False, 'detail': 'Answered without calling the tool. Choose a model with tool calling support.'}


RESPONSES_TOOL = {'type': 'function', 'name': 'report_ready', 'description': 'Report that you can call tools.',
                  'parameters': {'type': 'object', 'properties': {'ready': {'type': 'boolean'}}, 'required': ['ready']}}


async def responses_tool_check(base_url, model):
    """A local model through Codex: Codex speaks only the Responses API and drives every action through
    tool calls, so one tiny Responses request with one tool, to exactly this address and model, must come
    back as a function call. Catches a server without /responses and a model the server won't give tools
    (Ollama refuses tools for models whose template lacks them). Local and free; nothing is saved."""
    payload = {'model': model, 'input': 'Call the report_ready tool with ready set to true. Do not answer in text.',
               'tools': [RESPONSES_TOOL], 'tool_choice': 'auto', 'max_output_tokens': 1024, 'stream': False}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(CHECK_TIMEOUT, connect=3), trust_env=False) as client:
            response = await client.post(base_url.rstrip('/') + '/responses', json=payload)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return {'ok': False, 'detail': 'The server is not running at this address'}
    except httpx.TimeoutException:
        return {'ok': False, 'detail': f'No reply within {CHECK_TIMEOUT} seconds; the model may still be loading. Check again.'}
    except httpx.HTTPError as exc:
        return {'ok': False, 'detail': f'Could not reach the server ({type(exc).__name__})'}
    if response.status_code in (404, 405):
        return {'ok': False, 'detail': 'This server has no Responses API (/v1/responses), which Codex needs. Update it, or use llama.cpp llama-server.'}
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code != 200 or not isinstance(body, dict):
        error = body.get('error') if isinstance(body, dict) else None
        message = error.get('message') if isinstance(error, dict) else error if isinstance(error, str) else ''
        hint = ' For Ollama, choose a model listed with tool support, or serve the same file with llama.cpp --jinja.' if 'tools' in str(message) else ''
        return {'ok': False, 'detail': (f'The server refused a Responses tool request (HTTP {response.status_code})'
                                        + (f': {str(message)[:200]}' if message else '') + '.' + hint)}
    items = body.get('output') if isinstance(body.get('output'), list) else []
    if any(isinstance(i, dict) and i.get('type') == 'function_call' and i.get('name') == 'report_ready' for i in items):
        return {'ok': True, 'detail': 'Returned a structured tool call through the Responses API, as Codex uses it'}
    if 'report_ready' in json.dumps(items):
        return {'ok': False, 'detail': MARKUP_FIX}
    return {'ok': False, 'detail': 'Answered without calling the tool. Choose a model with tool calling support.'}


async def server_context(base_url, model, headers=None):
    """The context window the server runs this model with, from the server's own reports. Returns
    {'tokens': int | None, 'source': str}. Values are approximate: servers may round or cap them.
    Every probe stays on the connection's own origin, so a key in headers goes nowhere else."""
    base = base_url.rstrip('/')
    root = base.removesuffix('/v1')
    async with httpx.AsyncClient(timeout=httpx.Timeout(5, connect=2), trust_env=False, headers=headers or {}) as client:
        async def get_json(url, method='GET', body=None):
            try:
                response = await (client.post(url, json=body) if method == 'POST' else client.get(url))
                return response.json() if response.status_code == 200 else None
            except (httpx.HTTPError, ValueError):
                return None
        # vLLM and some gateways list it with the model.
        listed = await get_json(base + '/models')
        for entry in (listed or {}).get('data', []) if isinstance(listed, dict) else []:
            if isinstance(entry, dict) and entry.get('id') == model:
                for key in ('max_model_len', 'context_length', 'context_window'):
                    if isinstance(entry.get(key), int):
                        return {'tokens': entry[key], 'source': f'/models {key}'}
        # llama.cpp: the slot context the server was started with (-c).
        props = await get_json(root + '/props')
        if isinstance(props, dict):
            n_ctx = (props.get('default_generation_settings') or {}).get('n_ctx') or props.get('n_ctx')
            if isinstance(n_ctx, int) and n_ctx > 0:
                return {'tokens': n_ctx, 'source': 'llama.cpp /props n_ctx'}
        # LM Studio: the loaded context, else the model maximum.
        lmstudio = await get_json(root + '/api/v0/models/' + model)
        if isinstance(lmstudio, dict):
            for key in ('loaded_context_length', 'max_context_length'):
                if isinstance(lmstudio.get(key), int):
                    return {'tokens': lmstudio[key], 'source': f'LM Studio {key}'}
        # Ollama: a loaded model reports its context; otherwise a num_ctx set in the Modelfile.
        running = await get_json(root + '/api/ps')
        for entry in (running or {}).get('models', []) if isinstance(running, dict) else []:
            if isinstance(entry, dict) and entry.get('name') == model and isinstance(entry.get('context_length'), int):
                return {'tokens': entry['context_length'], 'source': 'Ollama /api/ps (loaded)'}
        shown = await get_json(root + '/api/show', 'POST', {'model': model})
        if isinstance(shown, dict):
            match = re.search(r'num_ctx\s+(\d+)', str(shown.get('parameters') or ''))
            if match:
                return {'tokens': int(match[1]), 'source': 'Ollama Modelfile num_ctx'}
    return {'tokens': None, 'source': 'not reported by this server'}


def served_by(response, data):
    """Which local server answered, on positive evidence only: llama.cpp marks its models owned_by
    'llamacpp'. MLX LM has no such marker (its entries are only id/object/created), so it stays unknown."""
    if any(isinstance(m, dict) and m.get('owned_by') == 'llamacpp' for m in data):
        return 'llamacpp'
    return ''


@functools.cache
def apple_silicon():
    """MLX needs Apple silicon; a Python running under Rosetta reports x86_64, so ask the kernel too."""
    if sys.platform != 'darwin':
        return False
    if platform.machine() == 'arm64':
        return True
    try:
        return subprocess.run(['sysctl', '-n', 'hw.optional.arm64'], capture_output=True, text=True, timeout=2).stdout.strip() == '1'
    except (OSError, subprocess.SubprocessError):
        return False


def listed_servers():
    """The local servers this computer can run: MLX servers only on Apple silicon Macs."""
    return [s for s in LOCAL_SERVERS if s[0] not in MAC_ONLY or apple_silicon()]


def local_rows(listed, probes, configured_bases):
    """One row per local server this computer can run. Servers sharing a port share one probe and
    only claim it on evidence: an identified server is Running and the others say it holds the port;
    an unidentified answer becomes one row for the address that names no server. A port only one
    listed server uses is credited to it, as before."""
    installed = {key: local_installed(key) for key, _, _, _ in listed}
    names = {key: label for key, label, _, _ in listed}
    rows, merged = [], set()
    for key, label, base, start in listed:
        result = dict(probes[base])
        found = result.pop('served_by', '')
        sharing = [k for k, _, b, _ in listed if b == base]
        owner = sharing[0] if len(sharing) == 1 else found if found in sharing else ''
        if result.get('reachable') and not owner:
            if base in merged:
                continue
            merged.add(base)
            rows.append({'id': 'shared:' + base, 'name': ' or '.join(names[k] for k in sharing), 'base_url': base, 'start_command': '',
                         'installed': any(installed[k] for k in sharing), 'identity_unconfirmed': True,
                         'configured': base.rstrip('/') in configured_bases, **result})
            continue
        if result.get('reachable') and key != owner:
            result = {'reachable': False, 'port_taken_by': names[owner], 'detail': f'{names[owner]} is using this port'}
        rows.append({'id': key, 'name': label, 'base_url': base, 'start_command': start, 'installed': installed[key],
                     'configured': key == owner and base.rstrip('/') in configured_bases, **result})
    return rows


def local_state(row):
    """One local_rows row as a built-in provider's status (task 6b673b03)."""
    if row.get('identity_unconfirmed'):
        return 'unidentified'
    if row.get('reachable'):
        return 'running'
    if row.get('port_taken_by'):
        return 'port_taken'
    return 'stopped' if row.get('installed') else 'not_installed'


def local_unavailable(info, row):
    """Why an agent cannot take this built-in provider now, and what the user does about it."""
    state = row.get('status')
    if state == 'unidentified':
        return (f'Something answers at {info["base_url"]} but did not say which server it is. Start {info["label"]} there, '
                'or add it in Team > Providers as a named server.')
    if state == 'port_taken':
        return f'{row.get("detail") or "Another server is using this port"}. Stop it or start {info["label"]} on another port.'
    if state == 'not_installed':
        # local_installed() only looks on PATH and in the usual app folders, so it cannot prove an absence.
        return (f'{info["label"]} isn\'t running, and it wasn\'t found on this computer. Install or start it with '
                f'`{info["start_command"]}`, then Refresh connections.')
    return f'{info["label"]} isn\'t running. Start it with `{info["start_command"]}`, then Refresh connections.'


def annotate_local(rows, runtime):
    """Give each local_servers row its built-in template, status and agents, and cache them for the agent editor
    (AgentProfiles.describe reads runtime.profiles.local_status). Returns {template: status entry}."""
    import provider_store
    agents = {}
    for pid, profile in runtime.profiles.items.items():
        agents.setdefault(profile.get('template'), []).append(pid)
    checked, cache = time.time(), {}
    for row in rows:
        state = local_state(row)
        templates = [provider_store.LOCAL_PREFIX + key for key, _, base, _ in listed_servers() if base == row['base_url']]
        own = provider_store.LOCAL_PREFIX + row['id'] if not row['id'].startswith('shared:') else None
        row.update(status=state, template=own, configured=True,
                   agents=sorted(a for t in (templates if own is None else [own]) for a in agents.get(t, [])))
        if own is None:
            row['templates'] = templates
        for template in templates if own is None else [own]:
            cache[template] = {'server': template.removeprefix(provider_store.LOCAL_PREFIX), 'status': state,
                               'detail': row.get('detail', ''), 'start_command': row.get('start_command', ''),
                               'base_url': row['base_url'], 'checked_at': checked}
    runtime.profiles.local_status = cache
    return cache


async def local_status(runtime):
    """Probe the local servers this computer can run and return each built-in provider's status."""
    listed = listed_servers()
    bases = list(dict.fromkeys(base for _, _, base, _ in listed))
    async with httpx.AsyncClient(timeout=httpx.Timeout(2, connect=1), trust_env=False) as client:
        results = await asyncio.gather(*(probe_server(client, base) for base in bases))
    return annotate_local(local_rows(listed, dict(zip(bases, results)), set()), runtime)


async def probe_configured(client, cfg):
    """A configured server's model list; a keyed one uses its own client carrying its key, after the
    key's origin check, and reports a missing, switched-off or misplaced key instead of probing."""
    if not cfg.get('key_target'):
        return await probe_server(client, cfg['base_url'])
    try:
        headers = provider_keys.headers(cfg)
    except provider_keys.KeyProblem as exc:
        return {'reachable': False, 'detail': str(exc)}
    async with httpx.AsyncClient(timeout=httpx.Timeout(6, connect=3), trust_env=False, headers=headers) as keyed:
        return await probe_server(keyed, cfg['base_url'])


async def probe_vendor(cfg):
    """S5b: an Anthropic or OpenAI key connection is checked by listing its models with the saved key
    (free), after the on/off and origin checks."""
    import provider_store
    try:
        return {'reachable': True, 'model_count': len(await provider_store.vendor_models(cfg))}
    except ValueError as exc:  # KeyProblem and FieldError carry a message safe to show
        return {'reachable': False, 'detail': str(exc)}


async def status(runtime):
    # Built-in local providers report through local_servers, one row each, not as configured connections.
    templates = {name: cfg for name, cfg in runtime.profiles.templates.items() if not cfg.get('local_server')}
    # Key connections for Claude or Codex (S5b) report their key, not the CLI's sign-in.
    vendors = {name: cfg for name, cfg in templates.items() if cfg.get('key_target') and cfg.get('provider') in provider_keys.VENDORS}
    # A local model through Codex reports its server, like an OpenAI-compatible connection, not a CLI sign-in.
    providers = sorted({cfg.get('provider', name) for name, cfg in templates.items() if name not in vendors and not cfg.get('local')} - {'api'})
    async with httpx.AsyncClient(timeout=httpx.Timeout(2, connect=1), trust_env=False) as client:
        configured = {name: cfg for name, cfg in templates.items() if (cfg.get('provider') == 'api' or cfg.get('local')) and cfg.get('base_url')}
        listed = listed_servers()
        bases = list(dict.fromkeys(base for _, _, base, _ in listed))
        cli_results, configured_results, local_results, vendor_results = await asyncio.gather(
            asyncio.gather(*(cli_status(p) for p in providers)),
            asyncio.gather(*(probe_configured(client, cfg) for cfg in configured.values())),
            asyncio.gather(*(probe_server(client, base) for base in bases)),
            asyncio.gather(*(probe_vendor(cfg) for cfg in vendors.values())))
    by_provider = dict(zip(providers, cli_results))
    by_template = dict(zip(configured, configured_results)) | dict(zip(vendors, vendor_results))
    rows = []
    for name, cfg in templates.items():
        provider = cfg.get('provider', name)
        catalog = runtime.controls.catalogs.get(next((p for p, prof in runtime.profiles.items.items() if prof['template'] == name), ''), {})
        row = {'template': name, 'provider': provider, 'label': cfg.get('label', name),
               'models': {'loading': bool(catalog.get('loading')), 'count': len(catalog.get('models', [])), 'error': catalog.get('error', '')}}
        if provider == 'api' or name in vendors or cfg.get('local'):
            row['server'] = {'base_url': cfg['base_url'], **by_template.get(name, {'reachable': False, 'detail': 'No base_url'})}
        else:
            row['cli'] = by_provider[provider]
        rows.append(row)
    local = local_rows(listed, dict(zip(bases, local_results)), set())
    annotate_local(local, runtime)
    return {'platform': sys.platform, 'providers': rows, 'local_servers': local}


def launch_sign_in(provider):
    with SIGN_IN_LOCK:
        return _launch_sign_in(provider)


def _launch_sign_in(provider):
    """Open a console running this file's sign-in helper. Returns launched=False with the command
    when no terminal can be opened (headless Linux), so the page can tell the user what to run."""
    native.command(provider)  # RuntimeError when the CLI is missing: nothing to open
    previous = SIGN_IN_WINDOWS.get(provider)
    if previous is not None and previous.poll() is None:
        return {'launched': False, 'already_open': True, 'command': SIGN_IN[provider]}
    argv = [sys.executable, str(Path(__file__).resolve()), 'sign-in', provider]
    here = str(Path(__file__).resolve().parent)
    if os.name == 'nt':
        SIGN_IN_WINDOWS[provider] = subprocess.Popen(argv, cwd=here, env=native.subscription_env(),
                                                     creationflags=subprocess.CREATE_NEW_CONSOLE)
        return {'launched': True, 'command': SIGN_IN[provider]}
    if sys.platform == 'darwin':
        # Terminal starts its own shell; the helper strips API-key variables itself.
        line = 'cd ' + shlex.quote(here) + ' && ' + shlex.join(argv)
        script = 'tell application "Terminal" to do script ' + json.dumps(line) + '\ntell application "Terminal" to activate'
        subprocess.Popen(['osascript', '-e', script], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'launched': True, 'command': SIGN_IN[provider]}
    for terminal in ('x-terminal-emulator', 'xterm'):
        if shutil.which(terminal):
            SIGN_IN_WINDOWS[provider] = subprocess.Popen([terminal, '-e', *argv], cwd=here, env=native.subscription_env(),
                                                         stdin=subprocess.DEVNULL, start_new_session=True)
            return {'launched': True, 'command': SIGN_IN[provider]}
    return {'launched': False, 'command': SIGN_IN[provider]}


def run_sign_in(provider):
    """Console side: run the official login interactively, then keep the window open to read the result."""
    label = LABELS.get(provider, provider)
    print(f'Signing in to {label} with its official command: {SIGN_IN[provider]}')
    print('Your password and token stay with the provider; ZeroCollab never sees them.')
    print('API keys set in your environment are ignored here.\n')
    if provider == 'antigravity':
        print('Antigravity signs in on first start. When its prompt appears you are signed in; close it to finish.\n')
    try:
        code = subprocess.call([*native.command(provider), *SIGN_IN[provider].split()[1:]], env=native.subscription_env())
    except (OSError, RuntimeError) as exc:
        print(f'Could not start {label}: {exc}')
        code = 1
    except KeyboardInterrupt:
        code = 130
    print('\nFinished. ZeroCollab is checking the sign-in now.' if code == 0 else f'\n{label} sign-in ended with exit code {code}.')
    try:
        input('Press Enter to close this window.')
    except (EOFError, KeyboardInterrupt):
        pass
    return code


def install(host, runtime):
    @host.app.get('/api/runtime/providers/status')
    async def providers_status():
        try:
            return await status(runtime)
        except Exception as exc:  # the Setup page shows the reason instead of a blank panel
            return JSONResponse({'error': f'{type(exc).__name__}: {exc}'[:400]}, 500)

    @host.app.post('/api/runtime/providers/{provider}/sign-in')
    async def providers_sign_in(provider: str):
        if runtime.deployment and runtime.deployment.rehearsal:
            return JSONResponse({'error': 'Sign-in is disabled in a rehearsal preview'}, 409)
        configured = {cfg.get('provider', name) for name, cfg in runtime.profiles.templates.items()}
        if provider not in SIGN_IN or provider not in configured:
            return JSONResponse({'error': f'No in-app sign-in for {provider}'}, 404)
        try:
            return await asyncio.to_thread(launch_sign_in, provider)
        except (OSError, RuntimeError) as exc:
            return JSONResponse({'error': str(exc)[:400]}, 400)

    # Add, edit, remove and reset connections (S3, provider_store.py), API keys (S5a) and the relay
    # that Claude and Codex use for Anthropic and OpenAI keys (S5b).
    import provider_relay
    import provider_store
    provider_keys.install(host, runtime)
    provider_store.install(host, runtime)
    provider_relay.install(host, runtime)


if __name__ == '__main__' and len(sys.argv) == 3 and sys.argv[1] == 'sign-in' and sys.argv[2] in SIGN_IN:
    raise SystemExit(run_sign_in(sys.argv[2]))
