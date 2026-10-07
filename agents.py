"""Agent trigger — writes to queue files picked up by visible worker terminals."""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class AgentTrigger:
    def __init__(self, registry, data_dir: str = "./data"):
        self._registry = registry
        self._data_dir = Path(data_dir)
        self.runtime = None

    def is_available(self, name: str) -> bool:
        return self._registry.is_registered(name)

    def get_status(self) -> dict:
        from mcp_bridge import is_online, is_active
        instances = self._registry.get_all()
        return {
            name: {
                "available": (not self.runtime.states[name].get("paused") and self.runtime.states[name]["state"] not in ("blocked", "login_required")
                              if self.runtime and self.runtime.handles(name) else is_online(name)),
                "busy": is_active(name),
                "label": info["label"],
                "color": info["color"],
            }
            for name, info in instances.items()
        }

    async def trigger(self, agent_name: str, message: str = "", channel: str = "general", **kwargs):
        """Write to the agent's queue file. The worker terminal picks it up."""
        if self.runtime and self.runtime.handles(agent_name):
            self.runtime.enqueue(agent_name, channel, message, kwargs.get("prompt", ""), kwargs.get('chain'), kwargs.get('routing'))
            return
        queue_file = self._data_dir / f"{agent_name}_queue.jsonl"
        self._data_dir.mkdir(parents=True, exist_ok=True)

        import time
        entry = {
            "sender": message.split(":")[0].strip() if ":" in message else "?",
            "text": message,
            "time": time.strftime("%H:%M:%S"),
            "channel": channel,
        }
        custom_prompt = kwargs.get("prompt", "")
        if isinstance(custom_prompt, str) and custom_prompt.strip():
            entry["prompt"] = custom_prompt.strip()

        with open(queue_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        log.info("Queued @%s trigger (ch=%s): %s", agent_name, channel, message[:80])

    def trigger_sync(self, agent_name: str, message: str = "", channel: str = "general", **kwargs):
        """Synchronous version of trigger - writes to queue file without async."""
        if self.runtime and self.runtime.handles(agent_name):
            self.runtime.enqueue_sync(agent_name, channel, message, kwargs.get("prompt", ""), kwargs.get('chain'), kwargs.get('routing'))
            return
        queue_file = self._data_dir / f"{agent_name}_queue.jsonl"
        self._data_dir.mkdir(parents=True, exist_ok=True)

        import time
        entry = {
            "sender": message.split(":")[0].strip() if ":" in message else "?",
            "text": message,
            "time": time.strftime("%H:%M:%S"),
            "channel": channel,
        }
        custom_prompt = kwargs.get("prompt", "")
        if isinstance(custom_prompt, str) and custom_prompt.strip():
            entry["prompt"] = custom_prompt.strip()

        with open(queue_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        log.info("Queued @%s trigger (ch=%s): %s", agent_name, channel, message[:80])
