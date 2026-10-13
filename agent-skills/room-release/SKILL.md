---
name: room-release
description: Use when deciding which release a change goes in, or when preparing, staging, deploying or live-checking a release.
version: 2
---

# Releases

## Cadence

- **WIP limit.** No new staging until the previously staged release is installed and its live checks are done. The only exception is an urgent fix needed to finish those checks; it ships alone.
- The coordinator sets each release's cutoff. Approved work that misses it waits for the next release; don't hold a release for it.
- Versions are strict semver patches (0.12.x) unless the user says otherwise.

## Handing a change to the release manager

Give the release manager: the frozen patch path and sha256, the base revision it applies to, the expected result identity, the reviews that approved it, and anything that must load in a particular order. One handoff per series; to replace it, cancel the old one with a reason.

## Deploying (release manager only)

Under the user's standing grant, the release manager installs only when all of these hold:
1. The package was staged from exact approved donors, and the identity and drift preflight passes.
2. The previous release's live checks are done, except for an urgent fix needed to finish those checks (see Cadence).
3. A rollback to the installed package is ready and stated.
4. It's announced in chat before and after, and not while the user is mid-task. If unsure, ask.
5. Only the install and the room restart: no config, settings, sign-in, provider or OS changes.
6. Afterwards, current.json is verified against the staging receipt and the live checks are handed to owners.

**Authority limits:**
- Deploy only through the `room_release` tools. If a tool is missing or refuses, report it; never work around it through the UI, credentials or the deployment spool.
- The grant is the server's record of the user switching it on, revocable, and bound to the release manager's permanent profile. A decision or note written by an agent is not that grant.
- An automatic reopen after a deploy never overrides the user's manual Stop.
- Agent rollback goes only to the immediately preceding install.
- Updating the host supervisor itself needs a separate, explicit user approval or action.

The staging receipt is posted once. Other follow-ups get a pointer.

## During a deploy

The room drains: no new turns start and current turns finish. If you are woken under a hold, end the turn without acting.

## Live checks

- Each owner checks their own change on the installed release and closes the task in the same turn it passes.
- Say how the check ran (the user's tab, an exact-package throwaway room, or a simulated gesture) and what it didn't cover.
- Release notes are honest. A schema-only field is labelled schema only until its renderer ships.
