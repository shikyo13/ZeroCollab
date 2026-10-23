"""Build an isolated desktop resource tree from canonical source. Never install or deploy."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python-root', type=Path, help='Bundled Python runtime for a distributable build')
    parser.add_argument('--production', action='store_true')
    args = parser.parse_args()
    if args.production and args.python_root is None:
        configured = os.environ.get('ZEROCOLLAB_PYTHON_HOME')
        if not configured:
            raise SystemExit('Set ZEROCOLLAB_PYTHON_HOME to the validated distributable Python runtime before packaging.')
        args.python_root = Path(configured)
    if args.python_root:
        args.python_root = args.python_root.resolve(strict=True)
        executable = args.python_root / ('python.exe' if sys.platform == 'win32' else 'bin/python')
        if not executable.is_file():
            raise SystemExit('The distributable Python runtime has no interpreter at ' + str(executable))
    resources = ROOT / 'build/desktop/resources'
    resources.mkdir(parents=True, exist_ok=True)
    backend = resources / 'backend'
    if backend.exists():
        if not backend.resolve().is_relative_to((ROOT / 'build').resolve()):
            raise ValueError('Build target escapes workspace')
        shutil.rmtree(backend)
    backend.mkdir()
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', 'node_modules')
    shutil.copytree(ROOT / 'src', backend / 'src', ignore=ignore)
    shutil.copytree(ROOT / 'apps/desktop/renderer/static', backend / 'apps/desktop/renderer/static', ignore=ignore)
    for name in ('agent-profiles', 'agent-skills'):
        shutil.copytree(ROOT / name, backend / name, ignore=ignore)
    for name in ('VERSION', 'config.toml', 'COLLABORATION.md', 'run.py', 'supervisor.py'):
        shutil.copyfile(ROOT / name, backend / name)
    for source in (ROOT / 'apps/desktop/host').glob('*.py'):
        shutil.copyfile(source, resources / source.name)
    if args.python_root:
        marker = resources / 'development.json'
        marker.unlink(missing_ok=True)
        destination = (resources / 'python').resolve()
        if not destination.is_relative_to((ROOT / 'build').resolve()) or args.python_root.is_relative_to(destination):
            raise ValueError('Python build target is unsafe or overlaps its input')
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(args.python_root, destination, ignore=ignore)
    else:
        python = ROOT / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
        if not python.is_file():
            raise SystemExit('Create .venv and install the project first: python -m venv .venv; .venv/Scripts/python -m pip install -e .')
        packages = subprocess.check_output([str(python), '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip()
        base = subprocess.check_output([str(python), '-c', 'import sys; print(sys._base_executable)'], text=True).strip()
        (resources / 'development.json').write_text(json.dumps({'python': base, 'packages': packages}), 'utf-8')
    print('Prepared desktop resources at ' + str(resources))


if __name__ == '__main__':
    main()
