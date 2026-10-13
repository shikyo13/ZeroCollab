---
name: pm-release-cycle
description: Use when a release is staged or installed, the user asks for status, or the board needs triage or rebalancing.
version: 3
---

# Release cycle and board triage (coordinator)

## When a release is staged

1. Confirm the cutoff matches the recorded decision. Name what's in it and what's held.
2. Check that each item has an owner and a live check written as an action on its task.

## When a release is installed

1. Verify it yourself: `data/deploy/current.json` (manifest and commit) and `VERSION` against the staging receipt.
2. Lift any hold. Wake only the owners who have live checks, each with the exact check.
3. Track every check to a closed task, or to a filed defect with an owner. An urgent fix found here ships alone.

## Status for the user

Short and plain, in this order: **done** (verified live), **in progress**, **blocked**, **needs you**. Say when you observed each fact. Never count source review or test output as done.

## Retrospective (after each release's checks)

1. Ask harness for a read-only health snapshot of the window:
   - turn outcomes per agent;
   - review answer time (median and p90);
   - the longest-open follow-ups;
   - repeated system lines;
   - per-agent tasks opened, finished, open now and working now (WIP), with anyone over one working task flagged.
   Skill use is planned; ask for it once approved profiles are running.
2. Name the top friction point from the numbers, not impressions.
3. Record one fix as a decision with an owner. Check that it held at the next release.

## Board triage

- Count open tasks by state: waiting on install, active build, blocked, deferred, standing.
- Close or merge duplicates and superseded work (`close_reason` plus pointer).
- Any agent with more than one working task: pick a main task, defer the rest with reasons.
- Stale tasks: update, defer with a reason, or ask the user.
- Persistent overload with a real capability gap: suggest one member in Team builder with the reason and expected deliverable.

## Asking the user

Only for decisions that are theirs: direction, hiring, deferrals, anything outside a recorded grant. Give a recommendation first, with the honest trade-off. Re-check a recommendation against their earlier statements before defending it.
