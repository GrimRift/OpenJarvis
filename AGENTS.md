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

## Web parity

For every feature and bug fix, classify Web impact first.

- `none`: state why; no Web work or Web test required.
- `affected`: use the shared implementation, update only necessary Web wiring,
  run focused tests, and perform one relevant Web/API smoke test.

Run the consolidated regression suite only before a release, at milestone completion, or after broad shared-core/security changes—not after routine features, bug fixes, configuration changes, or ordinary commits.
