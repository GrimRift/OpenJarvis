# OpenJarvis-Lab (Sage fork)

Milestones and scoped-but-unbuilt work live in `ROADMAP.md`; shipped work,
newest first, in `HANDOFF.md`.

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

### The suite is slow, not hung

`tests/tools tests/architecture` together took **10m43s** and looked stuck near
the end. It was not: the 11 architecture invariants accounted for 559s of it,
40–86s each, against 24s for the same suite run alone.

`_modules()` parses all 698 source files and every invariant called it, so the
whole tree was parsed eleven times per run. Alone that is ~2s a call; run after
`tests/tools`, with 998 tests' worth of imports and fixtures still on the heap,
each call inflated more than twentyfold. It is now `@cache`d (safe — every
caller only reads) and the collector is paused for the single parse. Combined
run: **643s → 132s**.

If it looks hung again, check `--durations` before assuming a deadlock.

### That list was hiding real bugs (2026-09-02)

Every entry below was re-checked against its actual cause rather than its
label. Several were not environmental at all:

- **`test_subprocess_sandbox` (7 cases) was a real defect.** `run_sandboxed`
  passed `preexec_fn=os.setsid` unconditionally, and `os.setsid` does not
  exist on Windows, so the sandbox raised `AttributeError` before starting
  anything. Now `start_new_session=True` on POSIX and
  `CREATE_NEW_PROCESS_GROUP` on Windows, with `taskkill /T` for the tree.
  All 11 pass. No production caller today, so it was latent, not live.
- **`test_file_permissions` is environmental, but the gap it hides is not.**
  `secure_create`/`secure_mkdir` are pure `os.chmod`, which on NTFS toggles
  only the read-only flag. Credentials pass through them while the names
  promise 0600. Skipped with that reason, and the gap is written into
  `file_utils`'s module docstring. Restricting properly on Windows (an
  `icacls` grant to the owner) is outstanding work.
- **`faiss` and `pdfplumber` are installed**, so failures naming them are not
  missing dependencies. `test_bm25` is a real defect: `BM25Memory` has no
  `clear`/`delete`, so the backend does not satisfy the store interface.
- **Google Tasks (403) is a disabled API, not a permissions problem (diagnosed 2026-09-04).** The same access token that reads Gmail and Calendar is rejected by Tasks, Drive and Contacts with `PERMISSION_DENIED: <API> has not been used in project 708083691179 before or it is disabled`. Re-consenting cannot fix it; enabling each API in the console can, in one click and with no re-auth. Left disabled at the user's request. The original note below predates that diagnosis:
- **Google Tasks (403) is deliberately left failing.** It is configured and
  the API refuses; that is a fault, not an absent credential. Oura, Strava
  and gemma.cpp *are* gated now, on the presence of their own config.
- `test_live_smoke` fails with `PermissionError [WinError 32]` — a SQLite
  handle not closed before the temp directory is removed.
- `test_integration_live` finds an empty skill catalogue.

The lesson: a standing-failures list earns its keep only if each entry's
cause is re-checked occasionally. This one had become a place bugs went to
be ignored.

### Known pre-existing test failures — do not chase

Confirmed environmental or order-dependent, identical on a clean tree:
`test_base_agent::test_default_max_turns`,
`test_persona_scope::test_named_persona_resolves_to_personas_dir`,
`test_channel_contract[whatsapp_baileys]`,
`test_claude_code::TestEnsureRunner` (2 cases),
`test_scan::test_run_quick_returns_subset`,
`test_credentials::test_file_permissions` (POSIX modes on Windows),
`test_data_boundary_audit::test_local_engine_vendor_model_names_are_not_cloud`
(3 parametrised cases) and
`test_data_boundary_audit::test_builtin_registry_tools_are_classified_or_explicitly_exempt`
(`check_class_schedule` and `notify_windows` have never been classified),
`test_cli::test_importing_cli_does_not_import_numpy`,
`TestGemmaCppLive` (needs Kaggle weights),
`test_new_connectors_live.py` (needs real Oura/Strava/Spotify credentials),
`test_check_class_schedule::test_check_class_schedule_registered` (passes in a
full run, fails alone),
`test_manager::TestCheckpoints` (one case fails per run and which one moves —
`test_checkpoint_retention_max_5` and `test_get_latest_checkpoint` have both
been seen; each passes when run alone).

`ruff check src/ tests/` is clean as of 2026-09-04 and is enforced by CI, so
lint the whole thing, not just your own files. The 12 remaining repo-wide
errors are all in `examples/twitter_bot/slack_preview.py`, which upstream's
lint job does not cover either. `ruff format --check` still fails on 68 files
(67 of them this fork's) and is deliberately not in CI.

`test_orchestrator::TestOrchestratorParallelTools::test_parallel_tool_execution`
joins that rotating set: it passes alone and with `tests/agents` alone, and
fails only when `tests/tools` has run first in the same process.

**The failing set rotates between runs.** `pytest-randomly` shuffles order and
several tests here leak state, so a wide run fails a different subset each
time — `test_git_tool`, `test_template_loader_security`,
`test_manager::TestCheckpoints` and `test_trace_recording` have all appeared,
and all pass in isolation. Before blaming a change for one of these, re-run
with `-p no:randomly` and compare against a stashed tree. A failure that moves
when the seed moves is pollution, not your diff.

### CI on this branch

`.github/workflows/sage-ci.yml`. Upstream's `ci.yml` triggers on `main` only,
and this fork's work never reaches `main`, so until 2026-09-04 nothing here had
ever been checked automatically -- which is how a red architecture invariant sat
unnoticed for a day.

That lane runs deliberately less than `ci.yml`: `ruff check src/ tests/`, the
frontend (vitest, `tsc --noEmit`, build), and the 16 python suites listed above
as passing. The suites it leaves out are named in the file with the reason for
each. Adding a suite back is the right way to close one of those gaps -- but
only once it is actually green, because the value of this lane is that red means
something.

Closing the remaining gaps is mostly test repair, not bug fixing. Of ~36
failures in the excluded suites, roughly nine touch code Sage actually runs; the
rest are `evals`, `install`, `pearl`, `mining`, `bench`, `learning` -- upstream
features this fork never executes. The six `tests/telemetry` failures assert
`0.0 > 0` and look alarming, but the live database has non-zero prefill/decode
energy: the mocks no longer match the implementation. Every bug that actually
cost the user something in the 2026-09-04 sessions was found by using Sage, not
by a test.

It runs with `-p no:randomly` on purpose. Under a shuffled order the failing set
rotates, so a fixed order is the difference between a signal and a coin flip.

## The bug this codebase keeps producing

An empty or absent value read as a deliberate answer. Nothing crashes; something
false is asserted confidently, which is far harder to notice than a failure.
Seven instances on 2026-09-04 alone:

- `.get("total", 0)` on a field the API renamed — a 63-track playlist reported as
  empty, and the model said playback had failed while music was playing.
- `DigestConfig.world` defaulting to `sources=[]`, so `.get(section, default)`
  returned the empty list and the briefing's whole world section collected
  nothing, for months, while showing as enabled.
- `savings?.total_calls ?? telemetry?.total_requests` — `??` passes through null
  and undefined but **not 0**, so a correct zero hid a real count.
- Tests reading ambient state as configuration: a populated data directory, the
  shell's API keys, the machine's timezone.
- `CredentialStripper` matching credentials by shape, so a key with no
  recognisable prefix passed through the one thing meant to catch it.

**The rule:** when a lookup can fail, decide explicitly what absent means. It is
almost never the same as zero, empty, or false. Prefer `or` over `??`/`.get(k, d)`
where an empty value should fall back, and make "unknown" a distinct state from
"none" in anything a user reads.

**Corollary for tests:** if a test passes here, ask what it would do on a clean
checkout with no credentials, no data directory, and a different timezone.

## Known traps in this codebase

Each of these cost a real debugging session. Check them before assuming a
component works:

- **The recurring shape is "the code was correct, it just was not the code
  being run."** A second send path, a third TTS call site, a fourth tool list.
  Behaviour tests keep passing because they exercise the path the author had in
  mind. `tests/architecture/test_invariants.py` and
  `frontend/src/architecture/invariants.test.ts` assert the wiring instead --
  read them before adding a new reply, speech, or tool path, and add an
  invariant when you create a fork someone could later miss. Write them against
  the AST, never the source text: this file and `InputArea.tsx` both contain
  comments naming the identifiers those tests forbid.

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
