---
name: room-intake
description: Use when the user asks for something new, or a request is too vague to assign, before turning it into a task.
version: 1
---

# Intake: turn a request into one task

Adapted from Matt Pocock's grilling and to-spec skills (github.com/mattpocock/skills, MIT), cut down for a user with little time for questions.

## 1. Find the facts yourself

Read the code, the board, decisions and the user's earlier messages, or ask the owning agent. Never ask the user something the room can find out.

## 2. Separate the decisions that are really the user's

The user's: what the product should do or look like, priorities between their own requests, anything outside a recorded grant. Ours: implementation, sequencing, routine trade-offs, anything an earlier decision already settles. Decide ours and say what you decided.

## 3. Ask once, numbered, with recommendations

If any of the user's decisions are open, ask them in one round:

- Number every question. Give each one your recommended answer, worded so "yes" accepts it.
- Ask only the questions whose parent decision is already settled. Ask follow-ups only if an answer opens new ones.
- If a choice is visual and can't be settled by words, have design build a quick clickable mock and ask the user to pick.
- If nothing needs the user, don't ask. Proceed and report what you decided.

## 4. Write the task as a short spec

- The request in the user's words, with the message id.
- What done looks like, as user-visible outcomes, numbered.
- The one place the check pokes at it, and the smallest check that proves it, on the user's real path.
- Owner, priority, what to read first, and what is out of scope.
- If it won't fit one owner's session, split it into tickets that each work end to end, with their blockers.

## 5. Plain words

Use the room glossary's terms. No run-together ids or private shorthand in anything the user reads.
