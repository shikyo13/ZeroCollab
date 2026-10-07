"""Open the official Gemini sign-in UI without inheriting API-key billing settings."""
import json
import subprocess
from pathlib import Path
from native_adapters import command, subscription_env

root = Path(__file__).resolve().parent
state = root / "data/workspace.json"
cwd = Path(json.loads(state.read_text("utf-8"))["worktrees"]["gemini"]) if state.exists() else root
print("Opening Google's official Antigravity CLI, the supported replacement for Gemini CLI.")
print("Complete its Google sign-in in your terminal/browser. Migration choices remain yours.")
raise SystemExit(subprocess.call(command("antigravity"), cwd=str(cwd), env=subscription_env()))
