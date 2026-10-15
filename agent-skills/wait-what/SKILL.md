---
name: wait-what
description: Use when Zero says "wait what", asks what a message of yours means, or says he can't follow it.
version: 1
---

# Wait what: say it again, plainly

Adapted from Matt Pocock's `wait-what` skill (github.com/mattpocock/skills, MIT). Zero didn't follow your last message. Re-pitch it so he can act on it in one read.

## Steps

1. **Find the message he means.** Usually your last one. If it's unclear which, take the most recent message of yours that he replied to.

2. **Rewrite it from his side.**
   - Start with what changed or what you need from him, in one sentence.
   - Then the reason, in one or two sentences.
   - Use the words in `Docs/GLOSSARY.md`, and spell out any other term the first time.
   - Say what he would see in the app, not what the code does: "the Board card shows your speech", not "runtime_focus moves the association".
   - Leave out hashes, IDs and file paths unless he asked for them. Keep at most one identifier he can click or search for.
   - Use short sentences and the active voice: one idea per sentence.
   - Keep the facts and their limits. Proposed, reviewed, installed and live-checked stay distinct, a planned step stays a plan, not a promise, and any limitation that matters stays in.

3. **End with the next step.** Either what happens next without him, or the single decision he needs to make, with your recommended answer worded so "yes" accepts it.

4. **Then fix the word, through design.** Send the plain reply first. If a term confused him and the glossary lacks it or defines it differently, send design a follow-up with the proposed glossary line. Design owns `Docs/GLOSSARY.md` changes.

## Done when

The re-pitch reads in under a minute, uses glossary words, and ends with a next step or one decision.
