"""Room-local model/effort preferences and official, read-only provider catalogs."""
import ast
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import native_adapters as native
from durable import atomic_write

EFFORT_ORDER = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra']


async def cli_catalog(provider, cwd):
    run = SimpleNamespace(worktree=Path(cwd), process=None, stderr='', stderr_task=None)
    rpc = None
    try:
        async with asyncio.timeout(20):
            if provider == 'codex':
                proc = await native.spawn(run, native.command(provider) + ['app-server', '--stdio', '-c', 'mcp_servers={}'])
                async def ignore(*args):
                    return {}
                rpc = native.Rpc(run, proc, ignore, ignore)
                await rpc.request('initialize', {'clientInfo': {'name': 'room_catalog', 'version': '0.1'}, 'capabilities': {}})
                await rpc.send({'jsonrpc': '2.0', 'method': 'initialized'})
                models, cursor = [], None
                for _ in range(4):
                    response = await rpc.request('model/list', {'includeHidden': False, 'cursor': cursor})
                    models.extend({'value': m['model'], 'label': m['displayName'],
                                   'efforts': [e['reasoningEffort'] for e in m.get('supportedReasoningEfforts', [])],
                                   'default_effort': m.get('defaultReasoningEffort', '')} for m in response['data'])
                    cursor = response.get('nextCursor')
                    if not cursor:
                        break
                return [{'value': '', 'label': 'CLI default', 'efforts': []}, *models]
            if provider == 'claude':
                proc = await native.spawn(run, native.command(provider) + ['--print', '--verbose', '--input-format', 'stream-json',
                    '--output-format', 'stream-json', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}'])
                proc.stdin.write((json.dumps({'type': 'control_request', 'request_id': 'catalog',
                                             'request': {'subtype': 'initialize', 'hooks': {}}}) + '\n').encode())
                await proc.stdin.drain()
                while line := await proc.stdout.readline():
                    message = json.loads(line)
                    if message.get('type') == 'control_response':
                        response = message.get('response', {}).get('response', {})
                        return [{'value': '' if m['value'] == 'default' else m['value'], 'label': m['displayName'],
                                 'resolved_model': m.get('resolvedModel', ''),
                                 'efforts': m.get('supportedEffortLevels', []) if m.get('supportsEffort') else []}
                                for m in response.get('models', [])]
                raise RuntimeError('Claude did not return its model catalog')
            if provider == 'antigravity':
                proc = await native.spawn(run, native.command(provider) + ['models'])
                proc.stdin.close()
                raw = await proc.stdout.read()
                await proc.wait()
                text = raw.decode('utf-8', 'replace')
                models = []
                for line in text.splitlines():
                    match = re.match(r'^\s*(gemini-[\w.-]+)\s+(.+?)\s*$', line)
                    if match:
                        models.append({'value': match[1], 'label': match[2], 'efforts': []})
                if not models:
                    raise RuntimeError('Antigravity returned no Gemini model choices. Refresh connections to reload its catalog.')
                families = {}
                for option in models:
                    variant = re.match(r'^(.*)-(low|medium|high)$', option['value'])
                    if variant:
                        families.setdefault(variant[1], {})[variant[2]] = option['value']
                for option in models:
                    variant = re.match(r'^(.*)-(low|medium|high)$', option['value'])
                    if variant:
                        variants = families[variant[1]]
                        option.update(efforts=[e for e in EFFORT_ORDER if e in variants],
                                      effort_models=variants, default_effort=variant[2])
                return models
            raise RuntimeError('Model discovery is not available for this provider')
    finally:
        if rpc:
            await rpc.close()
        await native.terminate(run, native.EXIT_GRACE)


class ProviderControls:
    def __init__(self, runtime):
        self.runtime = runtime
        self.path = runtime.data / 'provider_settings.json'
        self.values, self.catalogs = {}, {}
        self.saved = json.loads(self.path.read_text('utf-8')) if self.path.exists() else {}

    def register(self, name, cfg, restore_saved=True):
        initial = {'model': cfg.get('model', ''),
                   'effort': cfg.get('effort', cfg.get('request_options', {}).get('reasoning_effort', ''))}
        saved = self.saved.get(name, {}) if restore_saved else {}
        if isinstance(saved, dict):
            initial.update({k: v for k, v in saved.items() if k in initial and isinstance(v, str)})
        self.values[name] = initial
        # Loading is a state, not a failure: the UI shows it quietly and save stays blocked until choices arrive.
        self.catalogs[name] = {'models': [], 'error': '', 'loading': True, 'source': '', 'effort_note': ''}

    def describe(self, name):
        return {**self.catalogs[name], 'selected': dict(self.values[name])}

    async def refresh(self, name):
        state = self.runtime.states[name]
        try:
            self.catalogs[name] = await self.lookup(state['provider'], self.runtime.cfg['agents'][name], state['worktree'])
            return True
        except Exception as exc:
            detail = str(exc) or type(exc).__name__
            self.catalogs[name].update(error=detail[:500], loading=False)
            return False

    @staticmethod
    async def lookup(provider, cfg, cwd):
        """One provider connection's catalog; raises when the provider cannot list models.
        cwd is where an official CLI runs its model listing (an agent's worktree, or the room data
        folder for a provider no agent uses yet)."""
        if provider != 'api':
            models = await cli_catalog(provider, cwd)
            source = 'Official CLI model catalog'
            note = 'Effort options follow the selected model.'
            if provider == 'antigravity':
                note = 'Effort selects the matching preset in this Gemini model family. Provider default uses the preset shown in the model name.'
        else:
            base = cfg['base_url'].rstrip('/')
            efforts = cfg.get('effort_options', [])
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                response = await client.get(base + '/models')
                response.raise_for_status()
                ids = [m['id'] for m in response.json().get('data', [])]
                if not efforts:
                    try:
                        response = await client.get(base.removesuffix('/v1') + '/props')
                        if response.status_code == 200 and response.json().get('chat_template_caps', {}).get('supports_reasoning_effort'):
                            template = response.json().get('chat_template', '')
                            match = re.search(r'(?:resolved_)?reasoning_effort\s+not\s+in\s+(\([^()]+\))', template)
                            if match:
                                accepted = ast.literal_eval(match[1])
                                efforts = [level for level in EFFORT_ORDER if level in accepted]
                    except (httpx.HTTPError, SyntaxError, ValueError):
                        pass
            models = [{'value': value, 'label': value, 'efforts': efforts} for value in ids]
            source = 'Connected endpoint /models'
            note = 'The server exposes one loaded model.' if len(models) == 1 else 'Only models served by this endpoint are shown.'
            note += ' Effort is supported by its chat template.' if efforts else ' This endpoint has not advertised reasoning-effort support.'
        if not models:
            raise RuntimeError('Provider returned no model choices')
        return {'models': models, 'error': '', 'loading': False, 'source': source, 'effort_note': note}

    def validate(self, name, model, effort):
        if name not in self.values or not isinstance(model, str) or not isinstance(effort, str):
            raise ValueError('Choose a registered provider, model and effort')
        return self.check(self.catalogs[name], model, effort)

    @staticmethod
    def check(catalog, model, effort):
        """The model and effort must be in this catalog; also used for a provider no agent uses yet."""
        if not isinstance(model, str) or not isinstance(effort, str):
            raise ValueError('Choose a registered provider, model and effort')
        if catalog.get('loading'):
            raise ValueError('Provider choices are still loading; try again in a moment')
        if catalog['error']:
            raise ValueError(catalog['error'])
        option = next((m for m in catalog['models'] if m['value'] == model), None)
        if not option:
            raise ValueError('The chosen model is not in this provider\'s current catalog')
        if effort and effort not in option.get('efforts', []):
            raise ValueError('This model does not support the chosen effort level')
        return {'model': model, 'effort': effort}

    def change(self, name, model, effort):
        self.validate(name, model, effort)
        self.values[name] = {'model': model, 'effort': effort}
        content = json.dumps(self.values, indent=2)
        atomic_write(self.path, lambda stream: stream.write(content))
        if hasattr(self.runtime, 'profiles'):
            self.runtime.profiles.preferences(name, self.values[name])
        return dict(self.values[name])
