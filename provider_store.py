"""In-app provider connections (Docs/providers.md, slice S3).

config.toml (with config.local.toml) defines the room's provider templates. data/providers.json
holds what Team > Providers adds or changes, layered over them by id:
- an entry with a new id adds a provider connection;
- an entry with a config id overrides that connection's editable fields; Reset deletes the entry;
- {"removed": true} hides a config connection until it is reset.

Profiles copy their template when activated (AgentProfiles.config), so a change reaches an agent
only when it is reapplied here. Changes are refused while an agent on that connection is mid-turn,
so an in-flight turn's config is never touched. Ids never change; renaming edits only the label."""
import copy
import json
import re
import time
from urllib.parse import urlsplit

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

import provider_keys
from project_room import write_json

# Official CLIs a connection can use; each signs in with its own login (Team > Providers, S2).
SUBSCRIPTIONS = {
    'codex': {'command': 'codex', 'label': 'Codex', 'color': '#10a37f'},
    'claude': {'command': 'claude', 'label': 'Claude', 'color': '#da7756'},
    'antigravity': {'command': 'agy', 'label': 'Gemini (Antigravity)', 'color': '#4285f4'},
}
KINDS = (*SUBSCRIPTIONS, 'api')
ID = re.compile(r'[a-z][a-z0-9-]{0,39}')
# Path words under /api/runtime/providers/ that cannot be connection ids.
RESERVED = {'status', 'probe', 'check', 'keys'}
OUTPUT_TOKENS = (256, 32768)
DEFAULT_OUTPUT_TOKENS = 1024
COLOR = re.compile(r'#[0-9a-fA-F]{6}')
# S4 context budget. Characters per token is a conservative approximation (English and code run
# about 3 to 4); real tokenizers differ, so budgets are estimates, never hard guarantees.
CHARS_PER_TOKEN = 3
SAFETY_TOKENS = 256
CONTEXT_TOKENS = (2048, 2_000_000)
# The smallest room prompt worth sending (project_room.LIMIT_RANGES input_chars minimum).
MIN_INPUT_CHARS = 4000
# S5a: an API key connection's settings in providers.json. The key is only in the credential store.
KEY_FIELDS = ('key_target', 'key_hint', 'enabled', 'prices')
PRICE_LIMIT = 10_000  # USD per million tokens, an upper bound on what a typo can claim
MAX_PRICED_MODELS = 50


class FieldError(ValueError):
    """A refusal that belongs to one form field (context_tokens, key or model), so a form can mark that
    field instead of another: a missing context window must not make an accepted key look wrong."""

    def __init__(self, message, field):
        super().__init__(message)
        self.field = field


def input_budget_chars(context_tokens, output_tokens, fixed_chars):
    """Room prompt characters that fit after the response, the fixed instructions and tool
    definitions (fixed_chars) and a safety margin. Approximate."""
    reserve = output_tokens + -(-fixed_chars // CHARS_PER_TOKEN) + SAFETY_TOKENS
    return (context_tokens - reserve) * CHARS_PER_TOKEN


def fixed_chars_estimate():
    """Instructions and tool definitions every local turn carries, before the agent's own background."""
    import room_runtime
    from project_room import coordination_tools
    tools = len(json.dumps(room_runtime.local_tool_definitions() + coordination_tools()))
    collaboration = len((room_runtime.ROOT / 'COLLABORATION.md').read_text('utf-8'))
    return tools + collaboration + 2500  # room preamble and identity lines


def valid_key(value):
    """An API key as pasted, or a refusal that marks the key field."""
    try:
        return provider_keys.check_key(value)
    except ValueError as exc:
        raise FieldError(str(exc), 'key') from None


def server_address(value):
    """An OpenAI-compatible server address, normalised without a trailing slash."""
    parts = urlsplit(value.strip()) if isinstance(value, str) else None
    if not parts or parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError('Enter the server address as http://host:port/v1')
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('Enter the server address without a user name, password, query or fragment')
    return value.strip().rstrip('/')


def empty_catalog(error):
    return {'models': [], 'error': error[:500], 'loading': False, 'source': '', 'effort_note': ''}


class ProviderStore:
    def __init__(self, runtime):
        self.runtime = runtime
        self.path = runtime.data / 'providers.json'
        self.base = {name: copy.deepcopy(cfg) for name, cfg in runtime.cfg['agents'].items() if cfg.get('managed')}
        self.error = ''
        self.entries = self.read()

    def read(self):
        """A file that cannot be read is reported and never overwritten: writes stay refused until it is fixed."""
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text('utf-8'))
            entries = data['providers']
            if not isinstance(entries, dict):
                raise ValueError('providers is not an object')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.error = f'{self.path.name} could not be read ({exc}); fix or move it, then restart. Changes are disabled until then.'
            return {}
        return {name: entry for name, entry in entries.items() if ID.fullmatch(name) and isinstance(entry, dict)}

    def templates(self):
        merged = {}
        for name, cfg in self.base.items():
            entry = self.entries.get(name, {})
            if not entry.get('removed'):
                merged[name] = {**copy.deepcopy(cfg), **copy.deepcopy(entry), 'managed': True}
        for name, entry in self.entries.items():
            if name not in self.base and not entry.get('removed'):
                merged[name] = {**copy.deepcopy(entry), 'managed': True}
        return merged

    def source(self, name):
        if name not in self.base:
            return 'app'
        return 'edited' if name in self.entries else 'config'

    def describe(self):
        templates = self.runtime.profiles.templates
        rows = []
        for name, cfg in templates.items():
            rows.append({'id': name, 'provider': cfg.get('provider', name), 'label': cfg.get('label', name),
                         'color': cfg.get('color', '#8b5cf6'), 'source': self.source(name),
                         'base_url': cfg.get('base_url', ''), 'model': cfg.get('model', ''),
                         'max_tokens': cfg.get('max_tokens', DEFAULT_OUTPUT_TOKENS) if cfg.get('provider') == 'api' else None,
                         'context_tokens': cfg.get('context_tokens'), 'context_source': cfg.get('context_source', ''),
                         'tool_check': cfg.get('tool_check'), **self.key_view(name, cfg)})
        hidden = [{'id': name, 'label': cfg.get('label', name)} for name, cfg in self.base.items()
                  if self.entries.get(name, {}).get('removed')]
        connected = {cfg.get('provider') for cfg in templates.values()}
        return {'providers': rows, 'removed': hidden, 'error': self.error,
                'available': [{'provider': kind, 'label': info['label']} for kind, info in SUBSCRIPTIONS.items() if kind not in connected]}

    def key_view(self, name, cfg):
        """What the panel may know about an API key: never the key, at most its last four characters."""
        if not cfg.get('key_target'):
            return {'keyed': False}
        prices = cfg.get('prices') or {}
        return {'keyed': True, 'key_hint': cfg.get('key_hint', ''), 'enabled': cfg.get('enabled', True) is not False,
                'prices': prices, 'origin': provider_keys.origin(cfg.get('base_url', '')),
                'usage': provider_keys.summary(name, prices)}

    def offered(self, name):
        """Add agent offers a connection unless it is an API key connection that is switched off."""
        cfg = self.runtime.profiles.templates.get(name, {})
        return not (cfg.get('key_target') and cfg.get('enabled') is False)

    # ----- validation -----
    def fields(self, body, current=None):
        """The editable fields to store, checked. current is the existing connection when editing."""
        if not isinstance(body, dict):
            raise ValueError('Send the connection as an object')
        kind = current.get('provider') if current else body.get('provider')
        if current and body.get('provider', kind) != kind:
            raise ValueError('A connection keeps its provider; add a new connection instead')
        if kind not in KINDS:
            raise ValueError('Choose Codex, Claude, Antigravity or a local OpenAI-compatible server')
        defaults = SUBSCRIPTIONS.get(kind, {})
        label = body.get('label', (current or {}).get('label', defaults.get('label', '')))
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 60:
            raise ValueError('Give the connection a name between 1 and 60 characters')
        color = body.get('color', (current or {}).get('color', defaults.get('color', '#8b5cf6')))
        if not isinstance(color, str) or not COLOR.fullmatch(color):
            raise ValueError('Choose a six-digit hex color')
        out = {'provider': kind, 'label': label.strip(), 'color': color.lower()}
        if kind != 'api':
            out['command'] = defaults['command']
            return out
        base_url = server_address(body.get('base_url', (current or {}).get('base_url', '')))
        model = body.get('model', (current or {}).get('model', ''))
        if not isinstance(model, str) or not 1 <= len(model) <= 200:
            raise ValueError('Choose a model the server serves')
        output = body.get('max_tokens', (current or {}).get('max_tokens', DEFAULT_OUTPUT_TOKENS))
        if isinstance(output, bool) or not isinstance(output, int) or not OUTPUT_TOKENS[0] <= output <= OUTPUT_TOKENS[1]:
            raise ValueError(f'Maximum response length must be {OUTPUT_TOKENS[0]} to {OUTPUT_TOKENS[1]} tokens')
        out.update(type='api', base_url=base_url, model=model, max_tokens=output)
        # Context window: entered here, or None to read it from the server when the connection is checked.
        context = body.get('context_tokens', (current or {}).get('context_tokens'))
        if context is not None:
            if isinstance(context, bool) or not isinstance(context, int) or not CONTEXT_TOKENS[0] <= context <= CONTEXT_TOKENS[1]:
                raise FieldError(f'Context window must be {CONTEXT_TOKENS[0]:,} to {CONTEXT_TOKENS[1]:,} tokens, or left empty to read it from the server', 'context_tokens')
            out['context_tokens'] = context
            entered = 'context_tokens' in body and body['context_tokens'] != (current or {}).get('context_tokens')
            out['context_source'] = 'entered by you' if entered else (current or {}).get('context_source', 'entered by you')
        # Kept from the connection: its last check, request options (from config.toml) that every
        # turn and the tool-call check send, and an API key's settings (the key itself is never here).
        for key in ('tool_check', 'request_options', *KEY_FIELDS):
            if current and key in current:
                out[key] = current[key]
        if current and current.get('key_target') and provider_keys.origin(out['base_url']) != provider_keys.origin(current['base_url']):
            # The key is bound to its origin; a different server needs its own connection and key.
            raise ValueError('An API key connection keeps its server. To use another server, add a new API key')
        return out

    async def server_check(self, entry, headers=None):
        """S4 check for exactly this address and model: structured tool calls, and the context window the
        server reports when none was entered. Returns (tool_check, context) and never saves anything.
        headers: a key being checked; otherwise a saved key is attached after its origin check."""
        import provider_setup
        headers = provider_keys.headers(entry) if headers is None else headers
        tool = await provider_setup.tool_call_check(entry['base_url'], entry['model'], entry.get('request_options'), entry['max_tokens'], headers)
        tool = {**tool, 'base_url': entry['base_url'], 'model': entry['model'], 'checked': time.time()}
        context = await provider_setup.server_context(entry['base_url'], entry['model'], headers)
        return tool, context

    def budget_note(self, entry):
        """Refuse a context window too small for the room; otherwise describe the approximate prompt budget."""
        tokens = entry.get('context_tokens')
        if not tokens:
            return 'Context window unknown: the room uses its standard prompt budget. Enter it if replies fail with a context error.'
        budget = input_budget_chars(tokens, entry['max_tokens'], fixed_chars_estimate())
        if budget < MIN_INPUT_CHARS:
            raise FieldError(f'A context window of about {tokens:,} tokens leaves too little room once the response '
                             f'({entry["max_tokens"]:,} tokens), the room instructions and tool definitions are reserved. '
                             'Raise the server context (llama.cpp -c, Ollama num_ctx, LM Studio context length) or lower the maximum response length.',
                             'context_tokens')
        return f'About {budget // 1000}k characters of room context per turn, before the agent background (approximate)'

    def new_id(self, label, requested=None):
        taken = set(self.base) | set(self.entries) | RESERVED
        if requested is not None:
            if not isinstance(requested, str) or not ID.fullmatch(requested) or requested in taken:
                raise ValueError('Choose an unused id: a lowercase letter, then letters, digits or hyphens')
            return requested
        stem = re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')[:32] or 'provider'
        if not stem[0].isalpha():
            stem = 'provider-' + stem
        candidate, n = stem, 2
        while candidate in taken:
            candidate, n = f'{stem}-{n}', n + 1
        return candidate

    # ----- guards -----
    def gate(self):
        runtime = self.runtime
        if self.error:
            raise ValueError(self.error)
        if runtime.deployment and runtime.deployment.rehearsal:
            raise ValueError('Provider changes are disabled in a rehearsal preview')
        if runtime.deployment and runtime.deployment.reload()['writes'] == 'blocked':
            raise ValueError('Provider changes wait until the deployment check finishes')
        if getattr(runtime, 'repository_transition', None):
            raise ValueError('Wait for the repository change before changing providers')

    def members(self, name):
        """Active-project agents on this connection."""
        return [pid for pid, profile in self.runtime.profiles.items.items() if profile['template'] == name]

    def idle(self, name):
        busy = [self.runtime.profiles.items[pid]['name'] for pid in self.members(name) if pid in self.runtime.running]
        if busy:
            raise ValueError(f'Wait for {", ".join(busy)} to finish the current turn, then try again')

    def used_by(self, name):
        """Agents on this connection in every saved project, archived ones included."""
        users = set()
        for path in sorted((self.runtime.data / 'projects').glob('*/agents.json')):
            try:
                items = json.loads(path.read_text('utf-8'))
            except (OSError, ValueError) as exc:
                raise ValueError(f'Could not read the team of project {path.parent.name} ({exc}); not removing a connection it may use') from exc
            users.update(p.get('name') or p.get('id', '?') for p in items.values() if isinstance(p, dict) and p.get('template') == name)
        users.update(self.runtime.profiles.items[pid]['name'] for pid in self.members(name))
        return sorted(users)

    # ----- changes -----
    async def catalog(self, cfg, headers=None):
        """The connection's model list, checked against exactly these settings before saving."""
        try:
            return await self.runtime.controls.lookup(cfg.get('provider'), cfg, self.runtime.data, headers)
        except Exception as exc:
            raise ValueError(f'Could not list models from this connection: {str(exc) or type(exc).__name__}'[:400]) from exc

    def save(self):
        write_json(self.path, {'providers': self.entries})

    async def apply(self, name, catalog=None):
        """Load the merged templates, reapply them to idle active agents and publish."""
        runtime = self.runtime
        profiles = runtime.profiles
        profiles.templates = self.templates()
        if name not in profiles.templates:
            profiles.template_catalogs.pop(name, None)
        elif catalog is not None:
            profiles.template_catalogs[name] = catalog
        for pid in self.members(name):
            profile = profiles.items[pid]
            if name in profiles.templates and pid in runtime.states:
                cfg = profiles.config(profile)
                cfg['avatar_base'] = name
                runtime.cfg['agents'][pid] = cfg
                if catalog is not None:
                    runtime.controls.catalogs[pid] = dict(catalog)
                if not profile.get('archived'):
                    runtime.host.registry.register_managed(pid, cfg)
        await runtime.host.broadcast_agents()
        await runtime.publish_status()

    async def create(self, body):
        async with self.runtime.profile_lock:
            self.gate()
            entry = self.fields(body)
            templates = self.runtime.profiles.templates
            if entry['provider'] != 'api':
                existing = next((cfg.get('label', n) for n, cfg in templates.items() if cfg.get('provider') == entry['provider']), None)
                if existing:
                    raise ValueError(f'{SUBSCRIPTIONS[entry["provider"]]["label"]} is already connected as {existing}')
            catalog = await self.catalog(entry)
            note = ''
            if entry['provider'] == 'api':
                self.runtime.controls.check(catalog, entry['model'], '')
                note = await self.checked(entry)
            self.gate()  # a deployment check may have started during the lookup
            name = self.new_id(entry['label'], body.get('id'))
            self.entries[name] = entry
            self.save()
            await self.apply(name, catalog)
            return {'id': name, **entry, 'budget_note': note}

    async def checked(self, entry, headers=None):
        """Run the S4 server check on a local connection being saved and record it on the entry.
        The result is bound to exactly the address and model being saved; a failed tool check refuses
        the save. A context window read from the server fills an empty field."""
        if 'context_tokens' in entry:
            # An entered window too small for the room is refused before the tool call, which a key pays for.
            self.budget_note(entry)
        tool, context = await self.server_check(entry, headers)
        if not tool['ok']:
            raise FieldError(f'Tool-call check failed for {entry["model"]}: {tool["detail"]}', 'model')
        entry['tool_check'] = tool
        if 'context_tokens' not in entry and context['tokens']:
            entry['context_tokens'] = context['tokens']
            entry['context_source'] = f'read from the server ({context["source"]}); approximate'
        if 'context_tokens' not in entry:
            # An unknown window is not safe: Ollama, for one, silently drops the start of a prompt that
            # is too long, which removes the room instructions. The check above has just loaded the model,
            # so a server that can report its window usually already has.
            raise FieldError('This server did not report its context window. Enter it in the form: the context '
                             'the server runs this model with (llama.cpp -c, Ollama num_ctx or OLLAMA_CONTEXT_LENGTH, '
                             'LM Studio context length).', 'context_tokens')
        return self.budget_note(entry)

    def ready_to_commit(self, name):
        """Checked again after the last await, right before saving: turns are admitted without
        profile_lock, so an agent on this connection can start while a model list is loading.
        save() and apply() change nothing else before apply's first await, so check and commit
        happen without another turn being admitted in between."""
        self.gate()
        self.idle(name)

    async def edit(self, name, body):
        async with self.runtime.profile_lock:
            self.gate()
            current = self.runtime.profiles.templates.get(name)
            if current is None:
                raise ValueError('That provider connection was not found')
            self.idle(name)
            entry = self.fields(body, current)
            catalog = None
            note = ''
            if entry['provider'] == 'api':
                target_changed = (entry['base_url'], entry['model']) != (current.get('base_url', '').rstrip('/'), current.get('model'))
                if target_changed:
                    # A previous check or detected context belongs to the old address and model.
                    entry.pop('tool_check', None)
                    if 'context_tokens' not in body and entry.get('context_source', '').startswith('read from the server'):
                        entry.pop('context_tokens', None)
                        entry.pop('context_source', None)
                if target_changed or not entry.get('tool_check'):
                    catalog = await self.catalog(entry)
                    self.runtime.controls.check(catalog, entry['model'], '')
                    note = await self.checked(entry)
                else:
                    note = self.budget_note(entry)
            self.ready_to_commit(name)
            self.entries[name] = entry
            self.save()
            await self.apply(name, catalog)
            return {'id': name, **entry, 'budget_note': note}

    async def check(self, body):
        """The form's Check button: list models, test tool calls and read the context for an address and
        model without saving anything. Generates one request on that server, so the same gates apply."""
        self.gate()
        body = body if isinstance(body, dict) else {}
        entry = self.fields({**body, 'provider': 'api', 'label': body.get('label') or 'check'})
        current = self.runtime.profiles.templates.get(body.get('id')) if isinstance(body.get('id'), str) else None
        if current and current.get('key_target'):
            # Checking an API key connection being edited: its saved key, after the origin check.
            entry['key_target'] = current['key_target']
        catalog = await self.catalog(entry)
        self.runtime.controls.check(catalog, entry['model'], '')
        tool, context = await self.server_check(entry)
        if 'context_tokens' not in entry and context['tokens']:
            entry['context_tokens'] = context['tokens']
        try:
            note, fits = self.budget_note(entry), True
        except ValueError as exc:
            note, fits = str(exc), False
        return {'tool_check': tool, 'context': context, 'budget_note': note, 'fits': fits}

    # ----- API keys (S5a): OpenAI-compatible servers only; the room sends the key itself -----
    async def key_models(self, base_url, key, outcome='Key not saved; check it and try again.'):
        """The models a server lists with this key. Free; nothing is saved. Errors never echo the reply;
        outcome says what happened to the key."""
        import provider_setup
        valid_key(key)
        async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4), trust_env=False,
                                     headers=provider_keys.bearer(key)) as client:
            try:
                response = await client.get(base_url.rstrip('/') + '/models')
                response.raise_for_status()
                data = response.json().get('data')
            except (httpx.HTTPError, ValueError, AttributeError) as exc:
                raise FieldError(f'{provider_keys.failure(exc, base_url)} {outcome}', 'key') from exc
        ids = [m['id'] for m in data if isinstance(m, dict) and isinstance(m.get('id'), str) and m['id']] if isinstance(data, list) else []
        if not ids:
            raise FieldError(f'{provider_keys.host(base_url)} listed no models for this key. {outcome}', 'key')
        return ids[:provider_setup.MODEL_LIMIT]

    async def key_probe(self, body):
        """Check key: list the server's models with the key, so the form can offer them. Nothing is saved."""
        self.gate()
        body = body if isinstance(body, dict) else {}
        base_url = server_address(body.get('base_url', ''))
        models = await self.key_models(base_url, body.get('key'))
        warning = '' if base_url.startswith('https://') or provider_keys.loopback(base_url) else \
            'This address uses http, so the key would cross the network unencrypted.'
        return {'models': models, 'warning': warning}

    def key_prices(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict) or len(value) > MAX_PRICED_MODELS:
            raise ValueError(f'Prices must be an object of at most {MAX_PRICED_MODELS} models')
        prices = {}
        for model, price in value.items():
            if not isinstance(model, str) or not 1 <= len(model) <= 200 or not isinstance(price, dict):
                raise ValueError('Each price needs a model name and input, cached and output prices')
            row = {}
            for part in ('input', 'cached', 'output'):
                amount = price.get(part)
                if amount is None or amount == '':
                    continue
                if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not 0 <= amount <= PRICE_LIMIT:
                    raise ValueError(f'Prices are USD per million tokens, 0 to {PRICE_LIMIT:,}')
                row[part] = amount
            if row:
                prices[model] = row
        return prices

    async def key_create(self, body):
        """Check and save: the model list and the S4 tool-call check run with the key (the check is one
        small billed request). Only a key that passes is stored; the connection appears only then."""
        async with self.runtime.profile_lock:
            self.gate()
            body = body if isinstance(body, dict) else {}
            key = valid_key(body.get('key'))
            entry = self.fields({k: v for k, v in body.items() if k not in ('key', 'prices', *KEY_FIELDS)} | {'provider': 'api'})
            headers = provider_keys.bearer(key)
            if entry['model'] not in await self.key_models(entry['base_url'], key):
                raise FieldError(f'{entry["model"]} is not served for this key. Key not saved.', 'model')
            catalog = await self.catalog(entry, headers)
            note = await self.checked(entry, headers)
            prices = self.key_prices(body.get('prices'))
            self.gate()  # a deployment check may have started during the checks
            name = self.new_id(entry['label'], body.get('id'))
            entry.update(key_target=name, key_hint=key[-4:], enabled=True, prices=prices)
            provider_keys.save(self.runtime.data, name, key, entry['base_url'])
            self.entries[name] = entry
            try:
                self.save()
            except Exception:
                self.entries.pop(name, None)
                provider_keys.delete(self.runtime.data, name)
                raise
            await self.apply(name, catalog)
            return {'id': name, **{k: v for k, v in entry.items() if k != 'tool_check'}, 'budget_note': note}

    async def key_update(self, name, body):
        """Replace the key, turn the connection on or off, or set its prices. A replacement key is checked
        first; if it fails, the previous key and the switch stay exactly as they were."""
        async with self.runtime.profile_lock:
            self.gate()
            body = body if isinstance(body, dict) else {}
            current = self.entries.get(name)
            if not current or not current.get('key_target'):
                raise ValueError('That API key connection was not found')
            entry = copy.deepcopy(current)
            key = body.get('key')
            if key is not None:
                kept = 'Replacement not saved; the previous key is still in use.'
                valid_key(key)
                if entry['model'] not in await self.key_models(entry['base_url'], key, kept):
                    raise ValueError(f'{entry["model"]} is not served for this key. {kept}')
            if 'enabled' in body:
                if not isinstance(body['enabled'], bool):
                    raise ValueError('enabled must be true or false')
                entry['enabled'] = body['enabled']
            if 'prices' in body:
                entry['prices'] = self.key_prices(body['prices'])
            self.gate()
            if key is not None:
                provider_keys.save(self.runtime.data, name, key, entry['base_url'])
                entry['key_hint'] = key[-4:]
            self.entries[name] = entry
            self.save()
            await self.apply(name)
            if 'enabled' in body and body['enabled'] != current.get('enabled', True):
                # Agents on it show "API provider turned off" (or recover) from their connection check.
                await self.runtime.refresh()
            return {'id': name, **self.key_view(name, entry)}

    async def remove(self, name):
        async with self.runtime.profile_lock:
            self.gate()
            if name not in self.runtime.profiles.templates:
                raise ValueError('That provider connection was not found')
            self.idle(name)
            users = self.used_by(name)
            if users:
                raise ValueError('Used by ' + ', '.join(users) + '. Archive or remove those agents in every project first')
            keyed = (self.entries.get(name) or {}).get('key_target')
            if name in self.base:
                self.entries[name] = {'removed': True}
            else:
                self.entries.pop(name, None)
            self.save()
            if keyed:
                provider_keys.delete(self.runtime.data, keyed)  # removing a connection deletes its key
            await self.apply(name)
            return {'id': name, 'removed': True}

    async def reset(self, name):
        async with self.runtime.profile_lock:
            self.gate()
            if name not in self.base or name not in self.entries:
                raise ValueError('Only an edited or removed config.toml connection can be reset')
            self.idle(name)
            restored = self.base[name]
            try:
                catalog = await self.catalog(restored)
            except ValueError as exc:
                # Restored as configured even when its server is down. Never keep offering or
                # validating the overridden server's models; Refresh connections reloads them.
                catalog = empty_catalog(str(exc))
            self.ready_to_commit(name)
            self.entries.pop(name)
            self.save()
            await self.apply(name, catalog)
            return {'id': name, 'reset': True, **({'catalog_error': catalog['error']} if catalog['error'] else {})}


async def probe(body, templates=None):
    """Models a server serves, for the form's model list; nothing is saved. Editing an API key
    connection (id) lists them with its saved key, after the key's origin check."""
    import provider_setup  # imports this module's install(); resolved at call time
    body = body if isinstance(body, dict) else {}
    base_url = server_address(body.get('base_url'))
    current = (templates or {}).get(body.get('id')) if isinstance(body.get('id'), str) else None
    headers = provider_keys.headers({**current, 'base_url': base_url}) if current and current.get('key_target') else {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(6, connect=3), trust_env=False, headers=headers) as client:
        return await provider_setup.probe_server(client, base_url)


def install(host, runtime):
    store = runtime.profiles.store

    def answer(work):
        async def handler(*args):
            try:
                return await work(*args)
            except (ValueError, KeyError, TypeError) as exc:
                field = getattr(exc, 'field', None)
                return JSONResponse({'error': str(exc), **({'field': field} if field else {})}, 400)
        return handler

    @host.app.get('/api/runtime/providers')
    async def list_providers():
        return store.describe()

    @host.app.post('/api/runtime/providers')
    async def create_provider(request: Request):
        return await answer(store.create)(await request.json())

    @host.app.post('/api/runtime/providers/probe')
    async def probe_provider(request: Request):
        return await answer(probe)(await request.json(), runtime.profiles.templates)

    @host.app.post('/api/runtime/providers/check')
    async def check_provider(request: Request):
        return await answer(store.check)(await request.json())

    # S5a API keys. UI token only (run tokens reach only their coordination route); no route returns a key.
    @host.app.post('/api/runtime/providers/keys/probe')
    async def probe_key(request: Request):
        return await answer(store.key_probe)(await request.json())

    @host.app.post('/api/runtime/providers/keys')
    async def create_key(request: Request):
        return await answer(store.key_create)(await request.json())

    @host.app.post('/api/runtime/providers/keys/{name}')
    async def update_key(name: str, request: Request):
        return await answer(store.key_update)(name, await request.json())

    @host.app.post('/api/runtime/providers/{name}')
    async def edit_provider(name: str, request: Request):
        return await answer(store.edit)(name, await request.json())

    @host.app.post('/api/runtime/providers/{name}/remove')
    async def remove_provider(name: str):
        return await answer(store.remove)(name)

    @host.app.post('/api/runtime/providers/{name}/reset')
    async def reset_provider(name: str):
        return await answer(store.reset)(name)
