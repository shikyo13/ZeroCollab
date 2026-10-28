# Repository hygiene

- Keep maintained application code in `src/`, `apps/`, and `tools/`, build commands in `scripts/`, and repeatable checks in `tests/`. Commit durable source and tests before retiring a worktree.
- Use the existing package commands. Generated output belongs only in the ignored `build/` directory. Keep only the current successful desktop installer; packaging must discard temporary resources, unpacked apps, and earlier installers.
- Use `TemporaryDirectory`, context managers, or registered test cleanups for fixtures. Clean up on failure as well as success. Do not create named, permanent validation rooms or installation copies in the checkout.
- Never put another Git worktree inside a worktree or a build directory. Retire finished worktrees after preserving their commits and any unique uncommitted work. Do not delete live agent workspaces or user data to satisfy a cleanup rule.
- Keep private test observations, session transcripts, credentials, room databases, and internal working notes outside the source repository. Preserve unique records in the room's private validation-data location before deleting a fixture; do not keep its bundled application or dependencies.
- Do not retain older binary builds as source history. Use Git for source history. At task completion, remove disposable build and test copies and report any concrete reason cleanup could not finish.
