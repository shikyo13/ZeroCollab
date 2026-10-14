"""Agent registration can't be driven by a web page or used on a room agent (task 6be90c9a).

On 0.12.64 a no-cors text/plain POST from any site reached /api/register (loopback branch before the Origin
check, and request.json() ignores Content-Type), and registering a room agent's base renamed the live agent."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from registry import RuntimeRegistry


class RegisterGuard(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.registry = RuntimeRegistry(data_dir=tmp.name)
        self.registry.seed({'claude': {'label': 'Claude', 'color': '#da7756'}})
        self.registry.register_managed('backend', {'label': 'Backend', 'color': '#888888'})

    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import app as host
        application = FastAPI()
        patcher = patch.multiple(host, app=application, registry=self.registry, session_token='ui-token')
        patcher.start()
        self.addCleanup(patcher.stop)
        host._install_security_middleware('ui-token', {'server': {'port': 63501}})
        application.post('/api/register')(host.register_agent)
        return TestClient(application, base_url='http://127.0.0.1:63501', client=('127.0.0.1', 50000))

    def post(self, client, base, **headers):
        return client.post('/api/register', content=json.dumps({'base': base}),
                           headers={'Content-Type': 'text/plain', **headers})

    def test_a_browser_request_is_refused_before_anything_registers(self):
        client = self.client()
        for path in ('/api/register', '/api/deregister/claude', '/api/heartbeat/claude'):
            response = client.post(path, content='{"base": "claude"}',
                                   headers={'Origin': 'https://evil.example', 'Content-Type': 'text/plain'})
            self.assertEqual(response.status_code, 403, path)
        self.assertEqual(sorted(self.registry._instances), ['backend'])

    def test_a_room_agent_cannot_be_registered_by_a_wrapper(self):
        client = self.client()
        self.assertEqual(self.post(client, 'backend').status_code, 403)
        self.assertEqual(sorted(self.registry._instances), ['backend'])  # the live agent keeps its name
        self.assertEqual(self.post(client, 'claude').status_code, 200)  # a configured wrapper base still works

    def test_managed_marker_follows_activation(self):
        self.assertTrue(self.registry.is_managed('backend'))
        self.registry.deactivate_managed('backend')
        self.assertFalse(self.registry.is_managed('backend'))


if __name__ == '__main__':
    unittest.main()
