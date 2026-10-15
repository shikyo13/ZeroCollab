---
name: room-retro
description: Use when a task you own needed a rework round, a wrong claim or a failure someone else caught, or when Zero or the lead asks for a retro on a session or incident.
version: 1
---

# Retro: make the same mistake impossible next time

Adapted from Matt Pocock's `retro` skill (github.com/mattpocock/skills, MIT). A retro suggests changes to the room around the work: checks, skills, instructions and tools. It never fixes its own findings; the owner or the lead picks which to keep.

## Steps

1. **Read the record, not your memory.** Read the task (`room_task get`) with its history, reviews and follow-ups, the review findings, your own session log for the turns involved, and the room's runtime events for those runs. Each finding cites one of these.

2. **List what cost a round.** For each: what happened, the evidence, who or what caught it (Zero, a reviewer, a live check, a tool refusal) and how many turns it cost. Include your own mistakes first.

3. **Sort each finding.**
   - **Mechanical:** a machine could detect it. Examples: fixed text, a file or permission state, a missing field, a process running elevated, an environment flag, a size limit.
   - **Judgement:** it needs a reader. Examples: matching the surrounding style, choosing the right reviewer, deciding whether evidence is enough.

4. **Mechanical findings get a check.** Choose the cheapest place that already runs, in this order:
   1. the room tool that accepts the action, so it refuses at entry, the way `room_review` already refuses a critical with no failure text;
   2. an environment default the room sets for every agent process;
   3. a unit test in `tests/` that reads the real shipped files;
   4. a check inside a script that already runs, such as release verification or a kit's first line.

   Look for an existing check that is unwired or broken before proposing a new one. For each check, name the file or seam, what it refuses, its false-positive risk, and the smallest proof that it works. No new release machinery (decision 900d929d): a check inside existing verification needs the lead's agreement. A check warns or refuses one bad input. It never limits what an agent may do (Zero #18096).

5. **Judgement findings get a wording change.** Propose the smallest edit to the matching skill or role core, or remove a line that changes nothing. The description line of every skill is in every prompt, so keep it short.

6. **Hand over, don't apply.** Send one ranked list (worst first) as a `room_followup` to the lead, linked to the task. Each item names its owner and seam. Write the list down, as a file in your tree, before sending. Edit another agent's files only when they ask you to.

## Done when

Every finding cites a record, is sorted mechanical or judgement, and has an owner. Every mechanical finding names a check and its proof. The list is sent to the lead.
