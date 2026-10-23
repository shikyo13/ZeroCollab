"""Compatibility entry for isolated release rehearsals. Use npm start for the app."""
import os
import sys
from pathlib import Path

ROOM_WRITER_PROTOCOL = 1
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))

if __name__ == '__main__':
    if os.environ.get('ZC_REHEARSAL') != '1':
        raise SystemExit('The standalone web app is deprecated. Start the Electron app with npm start.')
    from zerocollab.api.run import main
    main()
