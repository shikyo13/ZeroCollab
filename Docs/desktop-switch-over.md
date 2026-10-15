# Switching to the ZeroCollab desktop app

Updated 14 October 2026. Daily R5 passed the real-room update and shared Browser checks and remains the live host; web remains the fallback. This accepts the mechanism, not the UI: the reported desktop appearance issues remain open in task `c1480b0d`.

## Current and staged Windows builds

The current live R5 executable is below. Keep this QA extraction and its accompanying files in place; do not move or replace them while it hosts the room.

```text
D:\Dev\Projects\ZeroCollab\runtime\data\worktrees\d458a9e5\zerocollab-c8e808154b08\qa\qa-scratch\live-handover-r5\live1\pkg\ZeroCollab\ZeroCollab.exe
```

The permanent R6 portable copy is staged at `D:\Dev\Projects\ZeroCollab\Desktop\Windows\R6\ZeroCollab.exe` (ZIP SHA prefix `620cc19b`, manifest SHA prefix `32e0f06c`). It has not been launched; security source review and QA visible native acceptance are pending. Wait for the R6 handoff before switching. Keep all portable-build files together.

## Start the day

1. If the web room is running, close it through the existing room controller first. Closing a browser tab does not stop it. Desktop and web must take turns hosting the room.
2. Open the app. It remembers and opens the selected room automatically. On first launch or to change rooms, use **Choose folder…** to select `D:\Dev\Projects\ZeroCollab\runtime`, the existing room folder. Do not select the source repository or its `data` subfolder.
3. Check that your saved project and chat are present. The room opens **Stopped**, with queued work cleared. Click **Resume** when ready; individually paused agents remain paused.

The app uses the existing room data and supported Codex/Claude CLI homes. R5 carries the room's existing automatic tool approval setting instead of forcing it off. If it refuses to open the room, keep the displayed error for the team rather than creating a replacement room or changing sign-ins.

## When a release installs

R5 selects the room's installed release when it opens and supports replacing that release while the app stays open. During an update, the window shows progress, then returns to the same view on the verified new release. A recovery failure stays visible in desktop; it does not automatically switch to web.

**Live mechanism accepted:** QA check `63dfd62e` passed the visible R5 working session and installed 0.12.69 to 0.12.70 update in the same shell, with a 25.8-second reconnect and no lost state. Desktop remains the live host; UI acceptance is separate.

Room releases do not update the Electron shell or its bundled deployment helpers. Those helpers are fixed copies included with this build. Changing them requires a deliberately rebuilt and reviewed desktop package, not an automatic shell update.

## Quit and use the web fallback

Closing the window hides it; the tray restores it. To give the room back, use **Quit** and confirm, then wait for the app to finish shutting down.

Quit stops the desktop host and backend. It does **not** automatically start the web room. If you want the fallback, open the web room through the existing room controller, check the saved project, then click **Resume**. It opens Stopped after desktop use so old queued work does not run on its own.

## Shared Browser and current limits

- R5 room sign-in for the shared Browser passed the signed-in check recorded in `ae3344c7`.
- This is an unsigned portable Windows build. There is no installer, signed distribution or supported macOS build yet.
