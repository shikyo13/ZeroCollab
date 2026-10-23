"""Built-in local providers and the Bonsai migration (task 6b673b03, contract local-providers/contract.md r1)."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import zerocollab.runtime.local_start as local_start
import zerocollab.providers.provider_setup as provider_setup
import zerocollab.providers.provider_store as provider_store
from zerocollab.runtime.agent_profiles import AgentProfiles
from zerocollab.providers.provider_store import FieldError, ProviderStore

BONSAI = {'type': 'api', 'managed': True, 'provider': 'api', 'base_url': 'http://127.0.0.1:8080/v1',
          'tool_schema_url': 'http://127.0.0.1:8080/tools', 'model': 'Bonsai-2-27B', 'max_tokens': 1024,
          'color': '#80b918', 'label': 'Bonsai',
          'request_options': {'reasoning_effort': 'medium', 'thinking_budget_tokens': 256}}
CODEX = {'command': 'codex', 'managed': True, 'provider': 'codex', 'label': 'Codex'}
SERVERS = (('ollama', 'Ollama', 'http://127.0.0.1:11434/v1', 'ollama serve'),
           ('lmstudio', 'LM Studio', 'http://127.0.0.1:1234/v1', 'lms server start'),
           ('llamacpp', 'llama.cpp', 'http://127.0.0.1:8080/v1', 'llama-server --jinja -m <model.gguf>'))


def room(folder, agents=None):
    data = Path(folder)
    for project, items in (agents or {}).items():
        (data / 'projects' / project).mkdir(parents=True)
        (data / 'projects' / project / 'agents.json').write_text(json.dumps(items), encoding='utf-8')
    return SimpleNamespace(data=data, cfg={'agents': {'codex': copy.deepcopy(CODEX), 'bonsai': copy.deepcopy(BONSAI)}})


def profile(pid, template, model=''):
    return {'id': pid, 'template': template, 'name': pid, 'color': '#123456', 'background': '', 'archived': False,
            'model': model, 'effort': ''}


def migrated(runtime):
    """The store as AgentProfiles builds it: read, then the one-time migration."""
    store = ProviderStore(runtime)
    if not store.error:
        store.migrate_local()
    return store


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        patcher = mock.patch.object(provider_setup, 'listed_servers', return_value=list(SERVERS))
        patcher.start()
        self.addCleanup(patcher.stop)


class MigrationTests(Base):
    def test_bonsai_becomes_a_llamacpp_model_in_every_project(self):
        runtime = room(self.folder, {'p1': {'bonsai': profile('bonsai', 'bonsai'), 'codex': profile('codex', 'codex')},
                                     'p2': {'tiny': profile('tiny', 'bonsai', 'other.gguf')}})
        store = migrated(runtime)
        p1 = json.loads((runtime.data / 'projects/p1/agents.json').read_text('utf-8'))
        p2 = json.loads((runtime.data / 'projects/p2/agents.json').read_text('utf-8'))
        self.assertEqual((p1['bonsai']['template'], p1['bonsai']['model']), ('local-llamacpp', 'Bonsai-2-27B'))
        self.assertEqual(p1['bonsai']['id'], 'bonsai')
        self.assertEqual(p1['codex']['template'], 'codex')
        self.assertEqual((p2['tiny']['template'], p2['tiny']['model']), ('local-llamacpp', 'other.gguf'))
        templates = store.templates()
        self.assertNotIn('bonsai', templates)  # hidden through the existing removed marker
        self.assertEqual(store.entries['bonsai'], {'removed': True})
        llama = templates['local-llamacpp']
        self.assertEqual((llama['label'], llama['tool_schema_url']), ('llama.cpp', BONSAI['tool_schema_url']))
        self.assertEqual(llama['models']['Bonsai-2-27B']['request_options'], BONSAI['request_options'])
        self.assertEqual(store.migration_notes, ['Bonsai is now a model of llama.cpp (Bonsai-2-27B). '
                                                 'Agents on it keep their identity and history.'])

    def test_entry_is_a_complete_api_connection_for_the_previous_release(self):
        runtime = room(self.folder, {'p1': {'bonsai': profile('bonsai', 'bonsai')}})
        migrated(runtime)
        saved = json.loads((runtime.data / 'providers.json').read_text('utf-8'))
        entry = saved['providers']['local-llamacpp']
        for key, value in (('provider', 'api'), ('base_url', BONSAI['base_url']), ('model', 'Bonsai-2-27B'),
                           ('max_tokens', 1024), ('request_options', BONSAI['request_options'])):
            self.assertEqual(entry[key], value)
        self.assertEqual(saved['migrations'], [provider_store.LOCAL_MIGRATION])

    def test_runs_once(self):
        runtime = room(self.folder, {'p1': {'bonsai': profile('bonsai', 'bonsai')}})
        migrated(runtime)
        again = migrated(runtime)
        self.assertEqual(again.migration_notes, [])
        self.assertEqual(again.entries['bonsai'], {'removed': True})

    def test_unreadable_project_postpones_everything(self):
        runtime = room(self.folder, {'p1': {'bonsai': profile('bonsai', 'bonsai')}})
        (runtime.data / 'projects/p2').mkdir()
        (runtime.data / 'projects/p2/agents.json').write_text('{broken', encoding='utf-8')
        store = migrated(runtime)
        self.assertIn('bonsai', store.templates())
        self.assertNotIn(provider_store.LOCAL_MIGRATION, store.migrations)
        p1 = json.loads((runtime.data / 'projects/p1/agents.json').read_text('utf-8'))
        self.assertEqual(p1['bonsai']['template'], 'bonsai')


class BuiltinTests(Base):
    def store(self):
        runtime = room(self.folder)
        runtime.cfg['agents'].pop('bonsai')
        return ProviderStore(runtime)

    def test_each_listed_server_is_a_provider(self):
        templates = self.store().templates()
        for template, label in (('local-ollama', 'Ollama'), ('local-lmstudio', 'LM Studio'), ('local-llamacpp', 'llama.cpp')):
            self.assertEqual((templates[template]['label'], templates[template]['provider']), (label, 'api'))

    def test_local_ids_are_reserved(self):
        store = self.store()
        with self.assertRaises(ValueError):
            store.new_id('x', 'local-mine')
        self.assertTrue(store.new_id('Local Box').startswith('provider-'))

    def test_named_server_at_a_running_builtins_address_is_refused(self):
        store = self.store()
        body = {'provider': 'api', 'label': 'Mine', 'base_url': 'http://127.0.0.1:11434/v1', 'model': 'm'}
        store.runtime.profiles = SimpleNamespace(local_status={'local-ollama': {'status': 'running'}})
        with self.assertRaises(FieldError) as caught:
            store.fields(body)
        self.assertEqual(caught.exception.field, 'base_url')
        # An answer the probe couldn't identify is usable only as a named connection, as its message says.
        store.runtime.profiles.local_status = {'local-ollama': {'status': 'unidentified'}}
        self.assertEqual(store.fields(body)['base_url'], 'http://127.0.0.1:11434/v1')

    def test_builtins_cannot_be_edited_or_removed(self):
        store = self.store()
        for call in (store.edit('local-ollama', {}), store.remove('local-ollama'), store.reset('local-ollama')):
            with self.assertRaises(ValueError):
                asyncio.run(call)

    def test_agent_config_takes_its_models_settings(self):
        runtime = room(self.folder, {'p1': {'bonsai': profile('bonsai', 'bonsai')}})
        runtime.controls = SimpleNamespace(saved={})
        profiles = AgentProfiles(runtime)
        cfg = profiles.config(profile('bonsai', 'local-llamacpp', 'Bonsai-2-27B'))
        self.assertEqual((cfg['max_tokens'], cfg['request_options']), (1024, BONSAI['request_options']))
        self.assertNotIn('models', cfg)
        other = profiles.config(profile('x', 'local-llamacpp', 'other.gguf'))
        self.assertEqual(other['max_tokens'], provider_store.DEFAULT_OUTPUT_TOKENS)
        self.assertNotIn('request_options', other)


class StatusTests(Base):
    def test_states(self):
        self.assertEqual(provider_setup.local_state({'reachable': True}), 'running')
        self.assertEqual(provider_setup.local_state({'reachable': False, 'port_taken_by': 'X'}), 'port_taken')
        self.assertEqual(provider_setup.local_state({'reachable': False, 'installed': True}), 'stopped')
        self.assertEqual(provider_setup.local_state({'reachable': False, 'installed': False}), 'not_installed')
        self.assertEqual(provider_setup.local_state({'reachable': True, 'identity_unconfirmed': True}), 'unidentified')

    def test_require_local_refuses_a_stopped_server_with_its_start_command(self):
        runtime = room(self.folder)
        runtime.profiles = SimpleNamespace(templates={})
        store = ProviderStore(runtime)
        stopped = {'local-ollama': {'status': 'stopped'}}
        with mock.patch.object(provider_setup, 'local_status', mock.AsyncMock(return_value=stopped)):
            with self.assertRaises(FieldError) as caught:
                asyncio.run(store.require_local('local-ollama', 'qwen3:8b'))
        self.assertEqual(caught.exception.field, 'template')
        self.assertIn('ollama serve', str(caught.exception))

    def test_require_local_refuses_a_model_the_server_does_not_list(self):
        runtime = room(self.folder)
        store = ProviderStore(runtime)
        runtime.profiles = SimpleNamespace(templates=store.templates())
        running = {'local-ollama': {'status': 'running'}}
        catalog = {'models': [{'value': 'qwen3:8b', 'label': 'qwen3:8b', 'efforts': []}]}
        with mock.patch.object(provider_setup, 'local_status', mock.AsyncMock(return_value=running)), \
                mock.patch.object(store, 'catalog', mock.AsyncMock(return_value=catalog)):
            with self.assertRaises(FieldError) as caught:
                asyncio.run(store.require_local('local-ollama', 'llama3:70b'))
        self.assertEqual(caught.exception.field, 'model')

    def test_status_refreshes_only_when_a_local_server_changes(self):
        runtime = SimpleNamespace(profiles=SimpleNamespace(items={}, local_status={}, local_probed=None, local_signature=None),
                                  profile_lock=asyncio.Lock())
        rows = [{'id': 'ollama', 'base_url': SERVERS[0][2], 'reachable': False, 'installed': True, 'models': []}]

        async def status(runtime):  # every probe (status, require_local, refresh) goes through annotate_local
            provider_setup.annotate_local(copy.deepcopy(rows), runtime)
            return {}

        async def refresh():  # RoomRuntime.probe_local records the probe the refresh applies
            await status(runtime)
            runtime.profiles.local_signature = runtime.profiles.local_probed
        runtime.refresh = mock.AsyncMock(side_effect=refresh)

        async def run():
            with mock.patch.object(provider_setup, 'status', status):
                await provider_setup.checked_status(runtime)  # before the first refresh: nothing applied yet
                self.assertEqual(runtime.refresh.await_count, 0)
                await runtime.refresh()  # the room's start
                await provider_setup.checked_status(runtime)  # unchanged
                self.assertEqual(runtime.refresh.await_count, 1)
                rows[0].update(reachable=True, models=['qwen3:8b'])  # Ollama started after the room
                await status(runtime)  # a save-time probe (require_local) sees it first and doesn't refresh
                await provider_setup.checked_status(runtime)
                self.assertEqual(runtime.refresh.await_count, 2)
                rows[0]['models'] = ['qwen3:8b', 'gemma3:4b']  # a model pulled while running
                await provider_setup.checked_status(runtime)
                self.assertEqual(runtime.refresh.await_count, 3)
        asyncio.run(run())

    def test_unreported_context_window_names_the_server_fix(self):
        runtime = room(self.folder)
        store = ProviderStore(runtime)
        runtime.profiles = SimpleNamespace(templates=store.templates())
        info = store.local_servers()['local-ollama']
        unknown = FieldError('This server did not report its context window. Enter it in the form', 'context_tokens')
        with mock.patch.object(store, 'checked', mock.AsyncMock(side_effect=unknown)):
            with self.assertRaises(FieldError) as caught:
                asyncio.run(store.check_local_model('local-ollama', info, 'qwen3:8b'))
        self.assertEqual(caught.exception.field, 'model')  # the agent editor has no context field
        self.assertIn('PARAMETER num_ctx', str(caught.exception))

    def test_reported_but_too_small_context_keeps_its_size(self):
        runtime = room(self.folder)
        store = ProviderStore(runtime)
        runtime.profiles = SimpleNamespace(templates=store.templates())
        info = store.local_servers()['local-lmstudio']

        async def small(probe, headers=None):  # what checked() does: record the read size, then budget_note refuses
            probe['context_tokens'] = 4096
            raise FieldError('A context window of about 4,096 tokens leaves too little room', 'context_tokens')
        with mock.patch.object(store, 'checked', small):
            with self.assertRaises(FieldError) as caught:
                asyncio.run(store.check_local_model('local-lmstudio', info, 'qwen3-8b'))
        self.assertEqual(caught.exception.field, 'model')
        self.assertIn('about 4,096 tokens', str(caught.exception))
        self.assertNotIn('did not report', str(caught.exception))
        self.assertIn('16384 or more', str(caught.exception))


class StartTests(Base):
    """Start buttons and the Antigravity sign-in (task edd41ff3)."""

    def test_antigravity_with_models_is_signed_in(self):
        cli = {'installed': True, 'sign_in': {'state': 'unknown', 'command': 'agy'}}
        loaded = provider_setup.antigravity_signed_in(cli, {'count': 3, 'error': ''})
        self.assertEqual((loaded['sign_in']['state'], loaded['sign_in']['billing']), ('signed_in', None))
        failed = provider_setup.antigravity_signed_in(cli, {'count': 0, 'error': 'not signed in'})
        self.assertEqual(failed['sign_in']['state'], 'unknown')

    def test_llamacpp_needs_a_saved_command_that_exists(self):
        self.assertFalse(local_start.spec('llamacpp', '')['available'])
        with self.assertRaises(ValueError):
            local_start.parse_command(str(Path(self.folder) / 'missing-server.exe'))
        program = Path(self.folder) / 'llama-server.exe'
        program.write_bytes(b'')
        with self.assertRaises(ValueError):  # the model must exist too
            local_start.parse_command(f'"{program}" -m "{Path(self.folder) / "gone.gguf"}"')
        self.assertTrue(local_start.spec('llamacpp', f'"{program}" --port 8080')['available'])

    def test_saved_command_makes_llamacpp_startable_off_path(self):
        runtime = room(self.folder)
        runtime.cfg['agents'].pop('bonsai')
        program = Path(self.folder) / 'llama-server.exe'
        program.write_bytes(b'')
        runtime.profile_lock, runtime.deployment, runtime.repository_transition = asyncio.Lock(), None, None
        store = ProviderStore(runtime)
        runtime.profiles = SimpleNamespace(store=store, items={}, local_status={}, templates={})
        start = asyncio.run(store.save_start_command('local-llamacpp', f'"{program}" --port 8080'))
        self.assertTrue(start['available'] and start['saved'])
        rows = [{'id': 'llamacpp', 'base_url': SERVERS[2][2], 'reachable': False, 'installed': False}]
        with mock.patch.object(provider_setup, 'local_installed', return_value=False):
            provider_setup.annotate_local(rows, runtime)
        self.assertEqual((rows[0]['status'], rows[0]['start']['available']), ('stopped', True))
        with self.assertRaises(FieldError):  # Ollama starts with its own app
            asyncio.run(store.save_start_command('local-ollama', 'ollama serve'))

    def test_migrated_bonsai_reads_not_running_not_not_installed(self):
        runtime = room(self.folder, {'p1': {'bonsai': profile('bonsai', 'bonsai')}})
        store = migrated(runtime)
        runtime.profiles = SimpleNamespace(store=store, items={}, local_status={})
        rows = [{'id': 'llamacpp', 'base_url': SERVERS[2][2], 'reachable': False, 'installed': False}]
        provider_setup.annotate_local(rows, runtime)
        self.assertEqual(rows[0]['status'], 'stopped')
        self.assertFalse(rows[0]['start']['available'])  # Start needs the saved command and its model


if __name__ == '__main__':
    unittest.main()
