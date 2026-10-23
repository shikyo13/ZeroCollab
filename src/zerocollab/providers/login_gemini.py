import sys as _entry_sys
from pathlib import Path as _EntryPath
if __package__ in ('', None):
    _entry_sys.path.insert(0, str(_EntryPath(__file__).resolve().parents[2]))

"""Open the official Gemini sign-in UI without inheriting API-key billing settings."""
import json
import subprocess
from pathlib import Path
from zerocollab.providers.native_adapters import command, subscription_env

from zerocollab.paths import ROOT as root
state = root / "data/workspace.json"
cwd = Path(json.loads(state.read_text("utf-8"))["worktrees"]["gemini"]) if state.exists() else root
print("Opening Google's official Antigravity CLI, the supported replacement for Gemini CLI.")
print("Complete its Google sign-in in your terminal/browser. Migration choices remain yours.")
raise SystemExit(subprocess.call(command("antigravity"), cwd=str(cwd), env=subscription_env()))
