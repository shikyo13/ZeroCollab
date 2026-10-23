"""Core startup must not import the optional media tool or expose a browser UI token."""
import subprocess
import sys
import textwrap
import unittest


class DesktopBoundaryChecks(unittest.TestCase):
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
