"""Package the desktop and retain only its current installer, never unpacked test copies."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def output_path(name):
    base = ROOT / 'build' / 'desktop'
    target = base / name
    if base.resolve() != ROOT.resolve() / 'build' / 'desktop' or target.resolve().parent != base.resolve():
        raise ValueError('Desktop build output escapes the workspace')
    if target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
        raise ValueError('Desktop build output must not be a link')
    return target


def discard(name):
    target = output_path(name)
    if target.exists():
        shutil.rmtree(target)


def publish():
    version = json.loads((ROOT / 'apps/desktop/electron/package.json').read_text('utf-8'))['version']
    names = ['latest.yml', f'ZeroCollab-Setup-{version}.exe', f'ZeroCollab-Setup-{version}.exe.blockmap']
    source = output_path('.package')
    if any(not (source / name).is_file() or not (source / name).stat().st_size for name in names):
        raise ValueError('Desktop packaging did not produce a complete installer')
    discard('.publish')
    stage = output_path('.publish')
    stage.mkdir(parents=True)
    for name in names:
        shutil.copy2(source / name, stage / name)
    current, previous = output_path('dist'), output_path('.previous')
    if previous.exists():
        raise ValueError('An interrupted publication needs recovery from build/desktop/.previous')
    if current.exists():
        current.rename(previous)
    try:
        stage.rename(current)
    except BaseException:
        if previous.exists():
            previous.rename(current)
        raise
    discard('.previous')
    return current


def main():
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    builder = ROOT / 'apps/desktop/node_modules/.bin' / ('electron-builder.cmd' if os.name == 'nt' else 'electron-builder')
    if not npm or not builder.is_file():
        raise SystemExit('Install locked desktop and renderer dependencies with npm ci before packaging.')
    try:
        discard('.package')
        subprocess.run([npm, '--prefix', str(ROOT / 'apps/desktop/renderer'), 'run', 'build'], check=True, cwd=ROOT)
        subprocess.run([sys.executable, str(ROOT / 'scripts/prepare_desktop.py'), '--production'], check=True, cwd=ROOT)
        subprocess.run([str(builder), '--config', 'electron-builder.json', '--win', 'nsis', '--x64', '--publish', 'never'],
                       check=True, cwd=ROOT / 'apps/desktop')
        print('Current desktop installer: ' + str(publish()))
    finally:
        for name in ('.package', '.publish', 'resources'):
            discard(name)


if __name__ == '__main__':
    main()
