"""Paths-only CLI home identity shared by web and desktop launchers."""
import json
import os
from pathlib import Path

from zerocollab.storage.durable import atomic_write

MARKER_VERSION = 1
MARKER_FILENAME = 'cli-homes.json'
MAX_HOME_CHARS = 32768
MAX_MARKER_BYTES = 256 * 1024
HOME_FIELDS = {'codex': ('CODEX_HOME', '.codex'), 'claude': ('CLAUDE_CONFIG_DIR', '.claude')}


def normalized_path(value):
    if not isinstance(value, str) or not value.strip() or '\0' in value or len(value) > MAX_HOME_CHARS:
        raise ValueError('CLI home must be a bounded path')
    # Lexical normalization only: no home contents, filesystem or process inspection.
    return os.path.normcase(os.path.abspath(value))


def effective_homes(env=None):
    """Resolve this process's overrides/defaults without opening any files."""
    env = os.environ if env is None else env
    user_home = env.get('USERPROFILE' if os.name == 'nt' else 'HOME', '')
    homes = {}
    for provider, (variable, default) in HOME_FIELDS.items():
        value = env.get(variable)
        if not value:
            if not user_home or not os.path.isabs(user_home):
                raise ValueError('CLI default user home is unavailable')
            value = os.path.join(user_home, default)
        homes[provider] = normalized_path(value)
    return homes


def validated_homes(homes):
    if not isinstance(homes, dict) or set(homes) != set(HOME_FIELDS):
        raise ValueError('CLI home marker requires Codex and Claude paths')
    result = {}
    for provider, value in homes.items():
        normalized = normalized_path(value)
        if not os.path.isabs(value) or len(normalized) > MAX_HOME_CHARS:
            raise ValueError('CLI home marker requires absolute bounded paths')
        result[provider] = normalized
    return result


def write_marker(data_dir, homes):
    """The web owner calls this only while holding the room writer lease."""
    marker = {'version': MARKER_VERSION, 'homes': validated_homes(homes)}
    text = json.dumps(marker, ensure_ascii=False, separators=(',', ':'))
    if len(text.encode('utf-8')) > MAX_MARKER_BYTES:
        raise ValueError('CLI home marker exceeds its size limit')
    atomic_write(Path(data_dir) / MARKER_FILENAME, lambda stream: stream.write(text))


def check_marker(data_dir, homes):
    """Return ok, missing or mismatch; never rewrite the web owner's marker."""
    expected = validated_homes(homes)
    try:
        with (Path(data_dir) / MARKER_FILENAME).open('rb') as stream:
            data = stream.read(MAX_MARKER_BYTES + 1)
        if len(data) > MAX_MARKER_BYTES:
            return 'mismatch'
        marker = json.loads(data)
        if (not isinstance(marker, dict) or set(marker) != {'version', 'homes'}
                or type(marker['version']) is not int or marker['version'] != MARKER_VERSION):
            return 'mismatch'
        return 'ok' if validated_homes(marker['homes']) == expected else 'mismatch'
    except FileNotFoundError:
        return 'missing'
    except (OSError, ValueError, TypeError, RecursionError):
        return 'mismatch'
