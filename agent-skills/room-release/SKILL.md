---
name: room-release
description: Use when a change is ready to ship, or after a release installs.
version: 4
---

# Releases

A release is ordinary build-and-ship:
1. Merge the reviewed change to main.
2. Build the installer with electron-builder. It carries the app and the room.
3. Copy latest.yml, the .exe and the .blockmap into the room's data/desktop-updates folder. electron-updater installs it and relaunches into the room.

- Ship when a milestone or a batch of fixes is ready. The coordinator names what goes in.
- Versions are strict semver patches unless the user says otherwise.
- No staging, trial installs, sign-off lists, file captures or extra release steps. Don't add release machinery.

## After it installs

Each owner checks their own change once: on the live room in the shared Browser, or in zc-test through zcvm. Never drive Zero's own screen. Record pass or fail on the task and close it. A failure gets one fix in the next build.
