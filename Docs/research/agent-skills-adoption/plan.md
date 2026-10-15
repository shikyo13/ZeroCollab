# Matt Pocock's agent skills in the room: adoption plan

Task 2060a58a, from Zero #19204. Source: github.com/mattpocock/skills at commit 49dd158d1076134a641b33efb035946536778336 (MIT), read from a shallow clone: `retro`, `grilling`, `grill-me`, `grill-with-docs`, `domain-modeling`, `wait-what`, `to-spec`, `to-tickets`, `implement`, `code-review`, `pr`, `writing-for-agents`, `prototype`, `handoff`.

How room skills work today: a skill is `agent-skills/<name>/SKILL.md` in the room source, checked by `agent_skills.read_skill`. The description must start "Use when" and fit 240 characters, and the body 16,000 characters. A skill reaches an agent only when Zero pins it in Team builder, and only the description line sits in every prompt.

## Standing rules this meets

- **Zero's decision fatigue.** Questions go to Zero only for decisions that are his: one numbered round, each with a recommended answer worded so "yes" accepts it. Agents find facts themselves.
- **Release pipeline frozen (900d929d).** Tests, tool refusals and environment defaults are fine. A check inside existing release verification needs the lead's agreement. No new release machinery.
- **No tests unless the human asks** (room rule) **and delivery over validation machinery** (Zero). This rules out test-first as a default, but not a single unit test that pins a shipped limit.
- **No app safety restrictions (Zero #18096).** A check refuses one bad input. It never limits what an agent may do.

## Per skill

| Skill | Decision | Room form | Owner | Reviewer |
|-|-|-|-|-|
| retro | Adapt now | `agent-skills/room-retro` (built). The first run is `retro-2026-10-14-normal-launch.md`. | harness | backend and systems for the checks |
| grilling, grill-me | Adapt | The lead's intake procedure: one round, only Zero's decisions, recommendations included. Not a per-agent skill, so agents don't all start interviewing Zero. | opus | harness |
| grill-with-docs, domain-modeling | Adapt | `Docs/GLOSSARY.md`, one word per room concept with words to avoid (Stop vs Pause vs Cancel update, manifest, receipt, donor, stage, rehearsal, R6). Created lazily, beginning with the terms that confused Zero today. | harness | design |
| wait-what | Adapt | A skill whose trigger is Zero saying "wait what" or asking what a message means: re-pitch the last message in plain words using the glossary. | harness | design |
| to-spec | Adapt | Task details written by the lead: problem, fix, numbered user stories, the agreed check seam. No separate spec document. | opus | harness |
| to-tickets | Already ours | Board tasks with `depends_on` are tickets with blocking edges. The useful rule: each task is a vertical slice that one agent can finish in one session. | opus | none |
| code-review (two axes) | Adapt | `room-review` gains one rule: the reviewer reports **Spec** findings (missing, unasked-for, looks done but wrong) and **Standards** findings separately, and never reranks across them. For large changes the lead may name two reviewers, one per axis. | harness | opus |
| pr (merge danger) | Adapt | Review requests and staging receipts state the door (one-way or two-way) and the blast radius in one line. | harness (room-review) | astra |
| implement, tdd | Skip | Conflicts with the room's no-tests-unless-asked rule. We keep the seam idea through the review's "smallest check". | none | none |
| prototype | Already ours | Mockups in the shared Browser and `room_markup` before building. | design | none |
| handoff | Already ours | Task check-ins and `next_action`. | none | none |
| writing-for-agents | Use as guidance | Applied when harness writes or prunes skills: keep description lines short, write positive instructions, delete lines that change nothing. Not installed as a skill. | harness | none |

## Order

1. room-retro: built, with the first retro handed to backend and systems.
2. The two-axis rule and merge danger in `room-review`: one revision of an existing skill.
3. The glossary and the wait-what skill, for design to review.
4. The lead's intake procedure (grill and spec): opus.

Each skill change ships in a release, then Zero pins it in Team builder. Until he pins it, an agent can still read the file in the repo.
