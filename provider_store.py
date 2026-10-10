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
from urllib.parse import urlsplit

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

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
RESERVED = {'status', 'probe'}
OUTPUT_TOKENS = (256, 32768)
DEFAULT_OUTPUT_TOKENS = 1024
COLOR = re.compile(r'#[0-9a-fA-F]{6}')


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
                         'max_tokens': cfg.get('max_tokens', DEFAULT_OUTPUT_TOKENS) if cfg.get('provider') == 'api' else None})
        hidden = [{'id': name, 'label': cfg.get('label', name)} for name, cfg in self.base.items()
                  if self.entries.get(name, {}).get('removed')]
        connected = {cfg.get('provider') for cfg in templates.values()}
        return {'providers': rows, 'removed': hidden, 'error': self.error,
                'available': [{'provider': kind, 'label': info['label']} for kind, info in SUBSCRIPTIONS.items() if kind not in connected]}

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
        base_url = body.get('base_url', (current or {}).get('base_url', ''))
        parts = urlsplit(base_url.strip()) if isinstance(base_url, str) else None
        if not parts or parts.scheme not in ('http', 'https') or not parts.hostname:
            raise ValueError('Enter the server address as http://host:port/v1')
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('Enter the server address without a user name, password, query or fragment')
        model = body.get('model', (current or {}).get('model', ''))
        if not isinstance(model, str) or not 1 <= len(model) <= 200:
            raise ValueError('Choose a model the server serves')
        output = body.get('max_tokens', (current or {}).get('max_tokens', DEFAULT_OUTPUT_TOKENS))
        if isinstance(output, bool) or not isinstance(output, int) or not OUTPUT_TOKENS[0] <= output <= OUTPUT_TOKENS[1]:
            raise ValueError(f'Maximum response length must be {OUTPUT_TOKENS[0]} to {OUTPUT_TOKENS[1]} tokens')
        out.update(type='api', base_url=base_url.strip().rstrip('/'), model=model, max_tokens=output)
        return out

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
    async def catalog(self, cfg):
        """The connection's model list, checked against exactly these settings before saving."""
        try:
            return await self.runtime.controls.lookup(cfg.get('provider'), cfg, self.runtime.data)
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
            if entry['provider'] == 'api':
                self.runtime.controls.check(catalog, entry['model'], '')
            name = self.new_id(entry['label'], body.get('id'))
            self.entries[name] = entry
            self.save()
            await self.apply(name, catalog)
            return {'id': name, **entry}

    async def edit(self, name, body):
        async with self.runtime.profile_lock:
            self.gate()
            current = self.runtime.profiles.templates.get(name)
            if current is None:
                raise ValueError('That provider connection was not found')
            self.idle(name)
            entry = self.fields(body, current)
            catalog = None
            if entry['provider'] == 'api' and (entry['base_url'], entry['model']) != (current.get('base_url', '').rstrip('/'), current.get('model')):
                catalog = await self.catalog(entry)
                self.runtime.controls.check(catalog, entry['model'], '')
            self.entries[name] = entry
            self.save()
            await self.apply(name, catalog)
            return {'id': name, **entry}

    async def remove(self, name):
        async with self.runtime.profile_lock:
            self.gate()
            if name not in self.runtime.profiles.templates:
                raise ValueError('That provider connection was not found')
            self.idle(name)
            users = self.used_by(name)
            if users:
                raise ValueError('Used by ' + ', '.join(users) + '. Archive or remove those agents in every project first')
            if name in self.base:
                self.entries[name] = {'removed': True}
            else:
                self.entries.pop(name, None)
            self.save()
            await self.apply(name)
            return {'id': name, 'removed': True}

    async def reset(self, name):
        async with self.runtime.profile_lock:
            self.gate()
            if name not in self.base or name not in self.entries:
                raise ValueError('Only an edited or removed config.toml connection can be reset')
            self.idle(name)
            self.entries.pop(name)
            self.save()
            catalog = None
            try:
                catalog = await self.catalog(self.base[name])
            except ValueError:
                pass  # restored as configured; Refresh connections reports the reason
            await self.apply(name, catalog)
            return {'id': name, 'reset': True}


async def probe(body):
    """Models a server serves, for the form's model list; nothing is saved."""
    import provider_setup  # imports this module's install(); resolved at call time
    base_url = body.get('base_url') if isinstance(body, dict) else None
    parts = urlsplit(base_url.strip()) if isinstance(base_url, str) else None
    if not parts or parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('Enter the server address as http://host:port/v1')
    async with httpx.AsyncClient(timeout=httpx.Timeout(4, connect=2), trust_env=False) as client:
        return await provider_setup.probe_server(client, base_url.strip())


def install(host, runtime):
    store = runtime.profiles.store

    def answer(work):
        async def handler(*args):
            try:
                return await work(*args)
            except (ValueError, KeyError, TypeError) as exc:
                return JSONResponse({'error': str(exc)}, 400)
        return handler

    @host.app.get('/api/runtime/providers')
    async def list_providers():
        return store.describe()

    @host.app.post('/api/runtime/providers')
    async def create_provider(request: Request):
        return await answer(store.create)(await request.json())

    @host.app.post('/api/runtime/providers/probe')
    async def probe_provider(request: Request):
        return await answer(probe)(await request.json())

    @host.app.post('/api/runtime/providers/{name}')
    async def edit_provider(name: str, request: Request):
        return await answer(store.edit)(name, await request.json())

    @host.app.post('/api/runtime/providers/{name}/remove')
    async def remove_provider(name: str):
        return await answer(store.remove)(name)

    @host.app.post('/api/runtime/providers/{name}/reset')
    async def reset_provider(name: str):
        return await answer(store.reset)(name)
