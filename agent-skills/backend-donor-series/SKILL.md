---
name: backend-donor-series
description: Use when a teammate's change touches backend-owned files.
version: 3
---

# Changes to backend files

Backend owns the room server files (for example `project_room.py`, `room_mcp.py`, `room_runtime.py` and `motion_agent.py`). A teammate who needs a change there sends backend a branch or patch.

1. Read it, and the callers of what it changes.
2. Review it once (room-review). If it's already approved, don't review it again.
3. Merge it to main with the author's attribution.
4. After the release installs, check your own part once and close your task.
