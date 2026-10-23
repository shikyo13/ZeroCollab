"""The desktop application is the supported interactive entry point."""
import subprocess
import sys
from zerocollab.paths import ROOT


def main():
    import shutil
    npm = shutil.which('npm.cmd' if sys.platform == 'win32' else 'npm')
    if not npm:
        raise SystemExit('Install Node.js, then run npm install and npm start in the repository.')
    # No user arguments are forwarded to the Windows npm batch launcher.
    raise SystemExit(subprocess.call([npm, 'start'], cwd=ROOT))


if __name__ == '__main__':
    main()
