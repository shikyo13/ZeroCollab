"""Core startup must not import the optional media tool or expose a browser UI token."""
import subprocess
import sys
import textwrap
import unittest
import json
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


def packaging_module():
    path = Path(__file__).resolve().parents[1] / 'scripts/package_desktop.py'
    spec = importlib.util.spec_from_file_location('package_desktop', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DesktopPackagingChecks(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.package = packaging_module()
        self.package.ROOT = self.root
        version = self.root / 'apps/desktop/electron/package.json'
        version.parent.mkdir(parents=True)
        version.write_text('{"version":"1.2.3"}')
        self.dist = self.root / 'build/desktop/dist'
        self.dist.mkdir(parents=True)
        (self.dist / 'old-installer.exe').write_bytes(b'old')

    def test_publication_replaces_old_build_with_only_current_install_files(self):
        source = self.root / 'build/desktop/.package'
        source.mkdir()
        names = ['latest.yml', 'ZeroCollab-Setup-1.2.3.exe', 'ZeroCollab-Setup-1.2.3.exe.blockmap']
        for name in names:
            (source / name).write_bytes(b'current')
        (source / 'win-unpacked').mkdir()
        (source / 'builder-debug.yml').write_text('debug')
        self.package.publish()
        self.assertEqual({p.name for p in self.dist.iterdir()}, set(names))
        self.assertFalse((self.dist.parent / '.previous').exists())

    def test_incomplete_package_preserves_current_installer(self):
        (self.dist.parent / '.package').mkdir()
        with self.assertRaisesRegex(ValueError, 'complete installer'):
            self.package.publish()
        self.assertEqual((self.dist / 'old-installer.exe').read_bytes(), b'old')

    def test_failed_build_discards_scratch_and_preserves_current_installer(self):
        builder = self.root / 'apps/desktop/node_modules/.bin' / (
            'electron-builder.cmd' if self.package.os.name == 'nt' else 'electron-builder')
        builder.parent.mkdir(parents=True)
        builder.write_bytes(b'fixture')
        for name in ('.package', '.publish', 'resources'):
            path = self.dist.parent / name
            path.mkdir()
            (path / 'temporary').write_bytes(b'temporary')
        with patch.object(self.package.shutil, 'which', return_value='npm'), \
             patch.object(self.package.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'npm')):
            with self.assertRaises(subprocess.CalledProcessError):
                self.package.main()
        self.assertEqual((self.dist / 'old-installer.exe').read_bytes(), b'old')
        for name in ('.package', '.publish', 'resources'):
            self.assertFalse((self.dist.parent / name).exists())

    def test_cleanup_refuses_paths_outside_desktop_build_directory(self):
        with self.assertRaisesRegex(ValueError, 'escapes'):
            self.package.discard('../..')
        self.assertTrue((self.dist / 'old-installer.exe').exists())


class DesktopBoundaryChecks(unittest.TestCase):
    def test_antigravity_replaces_an_interrupted_room_server(self):
        from zerocollab.providers import native_adapters
        with tempfile.TemporaryDirectory() as folder, patch.object(native_adapters, 'room_environment', return_value={}):
            run = SimpleNamespace(worktree=Path(folder), id='new-run')
            config = run.worktree / '.agents' / 'mcp_config.json'
            config.parent.mkdir()
            config.write_text(json.dumps({'mcpServers': {'zerocollab_coordination': native_adapters.room_server(run)}}))
            native_adapters.install_antigravity_room(run)
            servers = json.loads(config.read_text())['mcpServers']
            self.assertEqual(list(servers), ['zerocollab_coordination'])

    def test_native_room_mcp_uses_host_dependencies_in_an_isolated_base_python(self):
        from zerocollab.providers import native_adapters
        with patch.object(native_adapters, 'room_environment', return_value={}):
            server = native_adapters.room_server(SimpleNamespace())
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-03-26', 'capabilities': {},
            'clientInfo': {'name': 'desktop-validation', 'version': '1'}}}
        result = subprocess.run([sys._base_executable, *server['args']],
                                input=json.dumps(request) + '\n', capture_output=True,
                                text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = next(json.loads(line) for line in result.stdout.splitlines()
                     if json.loads(line).get('id') == 1)
        self.assertEqual(reply['result']['serverInfo']['name'], 'project_room')

    def test_core_configures_with_motion_imports_forbidden(self):
        script = textwrap.dedent('''
            import importlib.abc
            import sys
            class DenyMotion(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.startswith('zerocollab_motion'):
                        raise AssertionError('Core attempted optional media import: ' + fullname)
            sys.meta_path.insert(0, DenyMotion())
            import tempfile
            from pathlib import Path
            from fastapi.testclient import TestClient
            from zerocollab.api import app, run
            from zerocollab.runtime import room_runtime
            from zerocollab.paths import DESKTOP_STATIC
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                cfg = {'server': {'host': '127.0.0.1', 'port': 18312, 'data_dir': str(root / 'data')},
                       'images': {'upload_dir': str(root / 'uploads')}, 'agents': {},
                       'runtime': {'managed_updates': False, 'automatic_tool_approvals': False}}
                app.configure(cfg, session_token='fixture-secret', sessions=None)
                runtime = room_runtime.install(app)
                run.mount_desktop(app.app, DESKTOP_STATIC, session_token='fixture-secret')
                with TestClient(app.app, base_url='http://127.0.0.1:18312') as client:
                    assert client.get('/').status_code == 403
                    response = client.get('/', headers={'X-Session-Token': 'fixture-secret'})
                    assert response.status_code == 200
                    assert 'fixture-secret' not in response.text
                    assert client.get('/api/runtime').status_code == 403
                    assert client.get('/api/runtime', headers={'X-Session-Token': 'fixture-secret'}).status_code == 200
                assert not hasattr(app, 'media_jobs')
                assert not any(n.startswith('zerocollab_motion') for n in sys.modules)
        ''')
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
