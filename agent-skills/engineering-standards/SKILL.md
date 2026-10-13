---
name: engineering-standards
description: Use when writing, committing, freezing or reviewing code for this project, including CSS, build outputs and scene JSON.
version: 2
---

# Engineering standards

Reviewers enforce these, and a review can be refused on them. Harness samples recent donors against them in its health cycle.

## Before writing

- Read the existing implementation in full first. Port and reuse what is there; build from scratch only when nothing exists.
- Fix root causes, not symptoms.
- Add the error handling or validation the feature needs to work reliably, without being asked.

## Version control

- Small, focused commits with clear messages, in your own worktree.
- No unrelated edits riding along, and no committed scratch, check or proof artifacts.
- Built files (for example core.js, ui.js and ui.css) come only from their documented build command and options. Never hand-edit them, and they must reproduce byte for byte.

## Code

- No monolithic blocks: functions and modules with one job each.
- No inline styles for static presentation: use CSS classes and the app's design tokens. Values computed per frame or from data (transform, opacity, position, size, CSS custom properties) are set from code, and their constants are named.
- No inline magic numbers: name them as module-level constants or config.
- No repeated logic where a loop, a function or an existing library already does it.
- **One definition per contract.** When several layers check the same contract (schema, server, page, UI), derive them from one shared definition or module, and verify each layer against it. Separate copies drift: three allow-lists for file references once disagreed, and the page refused what the server accepted.
- **Deterministic rendering.** Rendered output is a pure function of the frame and its inputs. No wall clock, timers, CSS animations or transitions, or unseeded randomness in render paths, and seek order must not change pixels.
- User-visible motion honours reduced motion with the product's own reduced variant, not just none.

## Resources

- Caches are bounded, with clear keys and eviction. No unbounded lists or rings.
- Browsers, processes and handles are released deterministically, including on cancel. Media workers run at below-normal priority on Windows.
- Log through the module's logger at sensible levels; never log per frame.
- Temp files go in owned temp directories and are cleaned up. Anything that grows is rotated or capped. Never write outside owned paths.

## Verification

- Run the smallest meaningful check that proves the change end to end, through every consumer that reads it: the page, the UI, the installed release. Don't build test infrastructure larger than the change it checks.
- Report which checks ran, and their results, honestly.

## Text, encoding and Windows

- Text files are UTF-8 with LF. Read and write files with an explicit encoding (Python `read_text(encoding='utf-8')`), because the Windows default is cp1252.
- Output that a page inlines is ASCII-escaped.
- Never put Windows backslash paths inside bash heredocs or inline scripts. Use forward slashes or PowerShell.
- No em dashes in code, comments, docs, commit messages or chat.
