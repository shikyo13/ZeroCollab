---
name: backend-donor-series
description: Use when carrying a teammate's change into backend-owned files, or handing a patch series to the release manager.
version: 2
---

# Donor intake and series handoff (backend)

Backend owns room server files (for example `project_room.py`, `room_mcp.py`, `room_runtime.py` and `motion_agent.py`). Other agents change them by handing backend a reviewed donor; backend carries it and hands the release manager an exact series.

## Intake

1. Re-hash the donor patch and compare it with the sha256 the author gave. A mismatch stops intake.
2. Read the whole patch, plus every caller of the functions it changes, at the base you will carry it on.
3. Check that it applies with `git apply --check --whitespace=error-all` on that exact base: the installed or prepared release source, never a mutable working tree.
4. Review it (room-review skill), or record the existing approval that covers these exact bytes.
5. Apply it on your carrying branch as its own commit, keeping the author's attribution and the approval id in the message. Cut that branch from the current prepared or installed release source, not an older working branch, and confirm the carried result files hash the same as the author's.

## Exporting a series

1. Export the carried commits as a numbered series (`review-donors/series-N/0001-...patch`), one patch per reviewed change.
2. Record each patch's sha256 prefix and the approval that covers it. A re-export with identical content keeps its approval; say so.
3. Record the base revision, the result revision, and the files touched.
4. If a result file's identity depends on line endings, give both the LF and CRLF hashes and say which one the package should match.

## Handoff to the release manager

One follow-up per series, containing:
- the base revision and `git am` instructions;
- the expected result revision;
- each patch with its hash and approval;
- the files;
- the checks you ran;
- the live check you will do after install.

To replace a handoff, cancel the old follow-up with a reason, then create the new one. When a new series contains an earlier one unchanged, say so and cancel the earlier follow-up, so the release manager composes one series. Hold everything under the release WIP rule unless the coordinator calls it an urgent fix.

## After install

Run your live check on the installed release, post the evidence, and close your task in the same turn.
