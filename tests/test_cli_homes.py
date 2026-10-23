import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zerocollab.providers.cli_homes import MARKER_FILENAME, MAX_HOME_CHARS, MAX_MARKER_BYTES, check_marker, effective_homes, write_marker


class CliHomeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = {('USERPROFILE' if os.name == 'nt' else 'HOME'): str(self.root / 'fake-user')}
        self.homes = effective_homes(self.env)
        self.marker = self.root / MARKER_FILENAME

    def test_defaults_and_overrides_inspect_no_files(self):
        # No real homes, credentials or another process's environment are inspected.
        env = {**self.env, 'CODEX_HOME': str(self.root / 'elsewhere/../codex'),
               'CLAUDE_CONFIG_DIR': str(self.root / 'claude'), 'OPENAI_API_KEY': 'fake-secret'}
        with patch('builtins.open', side_effect=AssertionError('file inspected')), \
             patch.object(Path, 'open', side_effect=AssertionError('file inspected')), \
             patch.object(Path, 'resolve', side_effect=AssertionError('filesystem inspected')):
            homes = effective_homes(env)
        self.assertEqual(homes['codex'], os.path.normcase(str(self.root / 'codex')))
        self.assertEqual(set(homes), {'codex', 'claude'})
        self.assertFalse((self.root / 'fake-user').exists())

    def test_missing_matching_and_mismatch_never_rewrite(self):
        self.assertEqual(check_marker(self.root, self.homes), 'missing')
        self.assertFalse(self.marker.exists())
        write_marker(self.root, self.homes)
        before, stamp = self.marker.read_bytes(), self.marker.stat().st_mtime_ns
        self.assertEqual(check_marker(self.root, self.homes), 'ok')
        other = {**self.homes, 'codex': str(self.root / 'other')}
        self.assertEqual(check_marker(self.root, other), 'mismatch')
        self.assertEqual(self.marker.read_bytes(), before)
        self.assertEqual(self.marker.stat().st_mtime_ns, stamp)
        self.assertEqual(set(self.root.iterdir()), {self.marker})

    def test_malformed_version_and_oversize_refused_without_changes(self):
        for value in [b'not json', b'[' * 2000 + b'0' + b']' * 2000,
                      json.dumps({'version': True, 'homes': self.homes}).encode(),
                      json.dumps({'version': 1, 'homes': {'codex': 'relative', 'claude': '/tmp'}}).encode(),
                      b' ' * (MAX_MARKER_BYTES + 1)]:
            self.marker.write_bytes(value)
            self.assertEqual(check_marker(self.root, self.homes), 'mismatch')
            self.assertEqual(self.marker.read_bytes(), value)

    def test_invalid_writer_input_preserves_existing_marker(self):
        write_marker(self.root, self.homes)
        before = self.marker.read_bytes()
        for homes in [{}, {'codex': 'relative', 'claude': self.homes['claude']},
                      {**self.homes, 'codex': 'x' * (MAX_HOME_CHARS + 1)},
                      {**self.homes, 'codex': 'bad\0path'}]:
            with self.assertRaises(ValueError):
                write_marker(self.root, homes)
            self.assertEqual(self.marker.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
