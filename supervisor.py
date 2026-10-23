"""Compatibility host entry. New interactive sessions use the Electron desktop owner."""
import sys
from pathlib import Path

ROOM_WRITER_PROTOCOL = 1
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))

if __name__ == '__main__':
    from zerocollab.runtime.supervisor import main
    main()
