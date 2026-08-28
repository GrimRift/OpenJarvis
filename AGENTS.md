# OpenJarvis-Lab (Sage fork)

Branch: `feature/sage-customization`. Experimental fork, not production Sage
Architect. Live config: `C:\AI\OpenJarvis-Data\config.toml`.

## Fast start

- Inspect only files relevant to the request. Read `HANDOFF.md` when resuming
  roadmap work, checking current state, or explicitly asked; do not load it for
  unrelated routine edits.
- Preserve the dirty working tree and unrelated changes. Never reset/discard
  work or commit unless explicitly asked.
- Prefer existing code/config over new abstractions. Check
  `src/openjarvis/agents/` before adding an agent or loop.

## Commands

- Test: `uv run --frozen pytest <paths>`
- Lint: `uv run --frozen ruff check <paths>`
- CLI: `.venv\Scripts\jarvis.exe <command>`
- Restart Sage after code or config changes; it does not hot-reload.

## Tool pattern

Follow `src/openjarvis/tools/coding_command.py`: registered `BaseTool`,
`tool_id`, `ToolSpec`, `ToolResult`, configured allowed roots, resolved path
checks, and fail-closed behavior. Use `git_tool.py` for Git-specific patterns.

## Non-negotiable boundaries

- Filesystem/coding/Git writes deny access without configured
  `OPENJARVIS_*_DIRS`. Current configured root is `C:\AI`; never turn that into
  a permissive default. Preserve Python and Rust sensitive-file blocking.
- Keep `coding_command` allowlisted and `shell=False`; do not add unrestricted
  shell, PowerShell, cmd, REPL, interpreter, or Docker execution implicitly.
- `git_commit` requires explicit files: never allow `.`, wildcards, or unknown
  pre-staged content.
- Never ask for, enter, display, log, or persist credentials. The user sets
  keys/tokens in their own environment.
- Do not treat `requires_confirmation` as a security boundary; enforce safety
  inside each tool.
- Keep `DeepResearchAgent` (`deep_research.py`) separate from `ResearchAgent`
  (`research_loop.py`). Web search is Tavily-only with no silent fallback.
- Keep web citations as URLs and local knowledge citations on the existing
  numbered `ref_to_source` path.
- Do not reconnect Obsidian anywhere except `C:\AI\Sage-Vault` without asking.

## Working style

- Make narrow changes. Add comments only for non-obvious reasons.
- Run focused tests and Ruff first, then proportionate regressions/live checks.
- Check external config before assuming roadmap work needs source changes.
- Treat milestone/status details in older notes as stale until verified.

## Verifying a fix

A change is not done because the tests pass. It is done when the evidence
distinguishes "fixed" from "never broken":

- **Run every new regression test against the unfixed code and confirm it
  fails.** `git stash`, run it, `git stash pop`. A test written from a correct
  mental model often passes on the bug too, because it exercises a different
  path than the one that broke. This has happened here.
- **When a test needs the machine's real state, point it at `tmp_path`.**
  Several tools in this repo write to `OpenJarvis-Data` even with their
  side-effecting call patched. Exercising one against live data has corrupted
  real state during verification.
- **Check whether a failure pre-dates the change** before investigating it.
  Stash and re-run. This repo has a standing set of environmental failures
  (see below) and chasing them wastes a lot of time.
- Prefer one live end-to-end check of the actual reported symptom over more
  unit tests. Most real bugs here were found by using the app, not by testing.

### Known pre-existing test failures — do not chase

Confirmed environmental or order-dependent, identical on a clean tree:
`test_base_agent::test_default_max_turns`,
`test_persona_scope::test_named_persona_resolves_to_personas_dir`,
`test_channel_contract[whatsapp_baileys]`,
`test_claude_code::TestEnsureRunner` (2 cases),
`test_scan::test_run_quick_returns_subset`,
`test_credentials::test_file_permissions` (POSIX modes on Windows),
`test_cli::test_importing_cli_does_not_import_numpy`,
`TestGemmaCppLive` (needs Kaggle weights),
`test_new_connectors_live.py` (needs real Oura/Strava/Spotify credentials),
`test_check_class_schedule::test_check_class_schedule_registered` (passes in a
full run, fails alone). Repo-wide `ruff check .` is also not clean; lint only
the files you changed.

**The failing set rotates between runs.** `pytest-randomly` shuffles order and
several tests here leak state, so a wide run fails a different subset each
time — `test_git_tool`, `test_template_loader_security`,
`test_manager::TestCheckpoints` and `test_trace_recording` have all appeared,
and all pass in isolation. Before blaming a change for one of these, re-run
with `-p no:randomly` and compare against a stashed tree. A failure that moves
when the seed moves is pollution, not your diff.

## Known traps in this codebase

Each of these cost a real debugging session. Check them before assuming a
component works:

- **`jarvis serve` does not call `SystemBuilder.build()`.** It hand-assembles a
  `JarvisSystem` inline to dodge a ~30-40s double build, so **every dependency
  the builder injects is silently absent** unless separately wired. This has
  bitten three times: a systemless `TaskScheduler`, an untraced channel system,
  and a `RetrievalTool` with no backend. **When adding anything to
  `SystemBuilder`, check `serve.py` separately.**
- **A backend restart never picks up frontend changes.** `jarvis serve` serves
  a pre-built bundle from `server/static/`; run `cd frontend && npm run build`
  first. The PWA service worker can hold an even staler cache — a hard refresh
  or tab reopen may still be needed.
- **`TaskScheduler` cron is evaluated in UTC.** This machine is UTC+8, so
  `0 21 * * *` fires at 05:00 local. Convert before writing a schedule.
- **`SimpleAgent` cannot call tools at all** (single-turn). A scheduled
  "check X and notify me" task on `simple` produces text and does nothing.
  Use `orchestrator`.
- **Scheduled tasks get their tools from `config.toml`'s `[agent] tools`**, not
  from the task's own `--tools` flag, which is stored but unread.
- **The frontend has no jsdom and no testing-library** — vitest runs pure logic
  only, so a hook cannot be rendered. Extract decision logic into a pure
  function and test that; several real state-machine bugs were only testable
  after doing this.
- **Do not trust a small local model to call a tool reliably**, or to skip one
  when it should. `qwen3.5:4b` has fabricated notifications and hallucinated
  successful actions it never performed, and prompt wording did not fix it.
  Route deterministically (`_DIGEST_INTENT_RE`, `_SPOTIFY_TRANSPORT_RE`) or put
  the decision inside a single tool.
- **A tool the model cannot name, it will not call.** "check my inbox"
  produced no tool call at all while the tool existed and worked, because its
  description said only "Search the knowledge base". Tool descriptions are
  behaviour, not documentation.
- **Side effects belong with the action, never with the query.** A read-only
  checker that recorded "already notified" state silently cancelled a day of
  reminders when an unrelated task merely read the schedule.
- **A docstring is not evidence.** Two separate bugs here lived in modules
  whose docstrings confidently described the opposite behaviour. Read the code.
- **Config lives outside this repo** at `C:\AI\OpenJarvis-Data\config.toml`.
  Check it before concluding a feature needs source changes. An unquoted space
  in a TOML table header (`[Deepgram Flux]`) invalidates the whole file and
  breaks every unrelated credential in it.

## Web parity

For every feature and bug fix, classify Web impact first.

- `none`: state why; no Web work or Web test required.
- `affected`: use the shared implementation, update only necessary Web wiring,
  run focused tests, and perform one relevant Web/API smoke test.

Run the consolidated regression suite only before a release, at milestone completion, or after broad shared-core/security changes—not after routine features, bug fixes, configuration changes, or ordinary commits.
