# Sage Handoff for Claude Code

## Objective

Build Sage into a broadly capable Windows assistant. Expand access progressively with tests, backups, recoverable actions, and logs. Permanent blocks remain for passwords and credentials, security databases, protected Windows system areas, and irreversible deletion.

## Verified completed work

- **Sentence-buffered incremental TTS and long-history tool replay fixed (2026-08-31).** Voice-originated replies now keep streaming their exact model text into chat/history while a separate speech-only sanitizer emits completed sentences or safe clauses through one server-proxied Cartesia WebSocket context per assistant turn. Returned PCM stays ordered in the existing Web Audio queue; bounded frontend/server queues apply backpressure; Stop immediately clears scheduled/queued audio, cancels the Cartesia context, and rejects late frames without truncating chat. Pre-audio failures retain the safe batch fallback, while post-audio failures never replay from the beginning. Markdown, split URLs, paths, authentication codes, UUIDs, and long identifiers are held across model deltas until safely sanitized. Adaptive render batching restored token counters and prevents long streamed answers from making the chat UI laggy; the orb claims speaking only when audio is actually queued or playing, not while text alone is generating. A follow-up defect exposed by a 3,406-token tool-assisted answer was also fixed: the 8,000-token history window could retain a small `tool` result after dropping its large assistant `tool_calls` parent, producing OpenAI 400 on every later message. `LoopGuard` now repairs malformed history and treats each tool-call turn plus all results as an atomic unit, while `BaseAgent` no longer duplicates a persona-aware system prompt already assembled by the web route. The user accepted the voice behavior, and the same affected conversation shape was live-replayed after restart with HTTP 200, a complete SSE stream, assistant text, and no invalid-tool-history error.
- **Speech-only sanitization and speaking-orb playback lifecycle fixed (2026-08-31).** Sage now creates a separate sanitized string only at the TTS boundary: complete URLs, file paths, authentication codes, UUIDs, and long identifiers remain intact in chat text, stored conversation history, and internal tool results, while spoken audio refers to the exact value as visible in chat. The direct `text_to_speech` tool path now follows the same rule as voice replies, streaming speech, and morning digests. The orb no longer stops early because audio state is now owned per playback source; streaming TTS waits for the server to finish, the final browser audio source to end, and the browser/device output-latency tail before releasing its claim. Playback generations prevent stale callbacks or timers from clearing a newer utterance. Focused Python tests passed (**70**), the full frontend suite passed (**164**), Ruff and `git diff --check` were clean, the frontend production build succeeded, and the refreshed live Sage UI served the current bundle with backend health `ok` and no browser errors. The user then confirmed the behavior looked good.
- **Manual hidden Sage Start Menu launchers added and live-verified (2026-08-30).** Sage does not auto-start with Windows. The `Sage` shortcut manually runs `jarvis serve` on `127.0.0.1:8000` plus the Vite frontend on `127.0.0.1:5173`, waits for both health checks, and opens `http://localhost:5173` only when it actually started a missing component; clicking it while both are already healthy exits silently, avoiding duplicate browser tabs. A named mutex prevents overlapping launch attempts, which previously caused a false two-minute timeout after one competing Vite process had already claimed port 5173. The `Stop Sage` shortcut checks that each listener's command line belongs to `C:\AI\OpenJarvis-Lab` before stopping it and refuses an unrelated port owner. Version-controlled sources are `scripts/start-sage.ps1`, `scripts/start-sage-hidden.vbs`, `scripts/stop-sage.ps1`, and `scripts/stop-sage-hidden.vbs`; installed copies are under `C:\AI\OpenJarvis-Data\scripts\`, shortcuts are `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Sage.lnk` and `Stop Sage.lnk`, and logs are under `C:\AI\OpenJarvis-Data\logs\`. Start was live-verified with backend status `ok` and frontend HTTP 200; Stop was user-invoked and verified afterward with both endpoints unreachable, zero listeners on 8000/5173, zero Sage processes, and a clean Git worktree/index. No history, configuration, credentials, or stored data are deleted by either launcher.
- M22–M25 were consolidated in `76da347`: hardened explicit-file `git_commit`; fail-closed directory scoping; sensitive-file blocking in Python and Rust; Tavily-only web search; restored 35-test web-search coverage; Web-enabled Deep Research across both separate agent implementations; and live-tested Obsidian ingestion at `C:\AI\Sage-Vault`.
- Windows OAuth URLs now open shell-free without truncating `&` query parameters (`6b3b39c`). Google OAuth was live-tested successfully.
- Gmail OAuth ingestion, checkpoint-based incremental sync, and a configurable dynamic 12-calendar-month initial history window were committed in `d546669`. Existing older indexed mail, OAuth permissions, credentials, and checkpoints were preserved. Gmail ingestion and Deep Research were live-tested successfully.
- `AGENTS.md` contains concise repo guidance, security boundaries, and Web-impact classification.
- **`jarvis connect --sync` implemented** (`src/openjarvis/cli/connect_cmd.py`, uncommitted — see "Current Git state"): discovers sources via a new explicit-activation marker (`connectors/_activated.json`, `{"sources": [...]}`), not via `is_connected()`. This distinction matters: a single Google OAuth consent grants tokens for gmail/gcalendar/gdrive/gcontacts/google_tasks at once (`GOOGLE_ALL_SCOPES` in `oauth.py`), so `is_connected()` is `True` for all five even though the user only ever explicitly ran `jarvis connect gmail`. `--sync` only touches sources the user named directly, via `_mark_source_activated()` called at every successful `_connect_source()` branch (filesystem/oauth/token). Continues past individual source failures, reports per-source results in a table. Real machine state seeded with `{"sources": ["gmail", "obsidian"]}` (the only two ever explicitly connected per commit history) — do not add `gcalendar`/`gdrive`/`gcontacts`/`google_tasks` to that file without the user explicitly running `jarvis connect <source>` for one of them first. Live-verified: `jarvis connect --sync` correctly synced only gmail+obsidian (0 new chunks, both already fully ingested) and left the other four Google connectors untouched despite their valid tokens. 3 new tests in `tests/cli/test_connect.py` (skip-unactivated, continue-after-failure, empty-state message) plus `DEFAULT_CONFIG_DIR` mocking added to the two pre-existing OAuth tests, which were found — while doing this work — to have been silently writing real state to the user's actual `C:\AI\OpenJarvis-Data\connectors\` directory during test runs (a real, now-fixed test-hygiene bug, not caused by this feature but exposed by it once `_mark_source_activated()` was added to the same code path).
- **Outlook Mail via Microsoft Graph OAuth built and tested** (`src/openjarvis/connectors/oauth.py`, new `src/openjarvis/connectors/microsoft_auth.py`, full rewrite of `src/openjarvis/connectors/outlook.py`, `src/openjarvis/core/config.py`, all uncommitted): replaces the old IMAP/app-password `OutlookConnector` (which subclassed `GmailIMAPConnector`) with a real `auth_type="oauth"` connector against `graph.microsoft.com`. New `OAUTH_PROVIDERS["microsoft"]` entry mirrors Google's shared-provider pattern (`connector_ids`/`credential_files` fan-out — today just `("outlook",)` / `("microsoft.json", "outlook.json")`, structured to extend when Calendar/OneDrive are added). Scope deliberately narrow: `MICROSOFT_ALL_SCOPES = ["offline_access", "Mail.Read"]` only, per explicit user instruction — no send/delete/move/mark method exists anywhere in `outlook.py`, enforced by `test_no_send_delete_move_methods_exist`. `microsoft_auth.py` mirrors `google_auth.py`'s `current_access_token`/`refresh_access_token`/`call_with_refresh` shape, with one Microsoft-specific difference: refresh_token rotation is detected and persisted (Microsoft rotates it; Google doesn't). New `OutlookConnectorConfig` (`initial_sync_months: int = 12`) plumbed the same way Gmail's is. No CLI changes needed — Outlook rides the same generic `oauth` branch in `connect_cmd.py` already built for Gmail. New test files: `tests/connectors/test_outlook.py` (full rewrite, 12 tests), `tests/connectors/test_microsoft_auth.py` (8 tests), `tests/connectors/test_oauth_microsoft.py` (3 tests, confirms the microsoft.json+outlook.json fan-out). `tests/core/test_config.py` extended with the Outlook config default case.
  - **Live status: blocked on National University's Entra ID tenant, not on anything in this codebase.** The user hit two real external obstacles working through this: (1) their school account can't create App Registrations in NU's tenant at all (`401`/"you don't have access") — worked around by registering the app under a separate personal Microsoft account/tenant instead, since the app registration's owning tenant doesn't have to match the mailbox's tenant; (2) even after that, NU's tenant has **user consent disabled for external apps** — running `jarvis connect outlook` and signing in with the school mailbox produced "Request sent — your admin has been notified" instead of completing. `C:\AI\OpenJarvis-Data\connectors\outlook.json` currently holds only `client_id`+`client_secret` (written before the token exchange) — **no `access_token`, not connected, not in `_activated.json`**. Nothing more to do here until NU's IT approves the pending request, or the user points it at a non-NU mailbox instead. This is the same category of blocker flagged earlier as a Teams risk — turns out it also applies to plain `Mail.Read`, so treat any further Microsoft-tenant-scoped work (Calendar, OneDrive, Teams) as carrying the same risk until Outlook actually clears.
- **Spotify connected live** — no new code; the connector (`connectors/spotify.py`, `user-read-recently-played` scope only, read-only listening history) already existed in this codebase from a prior session and just needed the user to create a Spotify Developer app and run `jarvis connect spotify`. Confirmed via `_activated.json` (now `{"sources": ["gmail", "obsidian", "spotify"]}`) and a valid `spotify.json` token file. **Read-only by design** — no playback-control scope (`user-modify-playback-state`) or tools requested; user confirmed they want history-only for now, so "play a song through Sage" is explicitly not supported and would need a separate scope + new `mcp_tools()` entries if ever requested. **Now broken**: a later `jarvis connect --sync` run failed Spotify with `401 Unauthorized` on the refresh — token expired and refresh isn't working. User explicitly deferred fixing this ("do it later"); do not fix proactively, wait to be asked.
- **M26 first slice: proactive class-schedule Windows notification — implemented, tested, and live-verified end-to-end.** This is the first concrete "Sage acts on its own" feature, using the class schedule note at `C:\AI\Sage-Vault\Class Schedule.md` (created this session, synced via `jarvis connect --sync`, indexed as `obsidian:Class Schedule.md`, 6 chunks).
  - Two new tools, both uncommitted: `src/openjarvis/tools/notify_windows.py` (`NotifyWindowsTool`, sends a native Windows toast via `win11toast`'s `notify()` — deliberately NOT `toast()`, which blocks until the notification is clicked/dismissed; new optional dep `pyproject.toml`'s `notifications = ["win11toast>=0.35"]`) and `src/openjarvis/tools/check_class_schedule.py` (`CheckClassScheduleTool`, deterministic — parses the `## Subjects` markdown table and does all date/time comparison in real Python `datetime`, not LLM reasoning, specifically because LLM date arithmetic is unreliable; returns upcoming classes within a `lookahead_minutes` window, default 15; dedupes via a JSON state file at `get_data_dir()/class_schedule_notify_state.json` so the same occurrence isn't re-notified every poll). Both registered in `tools/__init__.py`'s import block and added to `C:\AI\OpenJarvis-Data\config.toml`'s `[agent] tools = "..."` line (required — `jarvis scheduler run-task` calls `system.ask(prompt, agent=agent)` with no `tools=` argument, so tool availability comes from the agent's *global* configured tool list, not anything passed at task-creation time).
  - **Real infrastructure bug found and deliberately routed around, not fixed**: `openjarvis.scheduler.TaskScheduler`'s in-process cron/interval poll loop is dead code — `SystemBuilder._setup_scheduler()` never wires a real `system` into it, and `jarvis serve` never starts its background thread at all. Fixing that was explicitly scoped out as a bigger, riskier core change. Instead, this uses the already-fully-working `jarvis scheduler run-task <agent>` CLI command (`cli/scheduler_cmd.py:229`, builds a real system on demand, no dependency on `jarvis serve` running), driven by a **native Windows Task Scheduler entry** (`schtasks`, task name `Sage-ClassScheduleCheck`, runs every 5 minutes). One correction made mid-implementation: `--agent simple` doesn't tool-call at all (`SimpleAgent` is single-turn) — the task uses `--agent orchestrator`.
  - A live scheduled task is registered (`jarvis scheduler create ...`, id shown via `jarvis scheduler list`, `--type interval --value 300 --agent orchestrator --tools "check_class_schedule,notify_windows"` — the `--type`/`--value`/`--tools` flags are stored for documentation/forward-compat only, since `run-task` ignores them today per the dead-poll-loop issue above).
  - **Two real bugs found and fixed while live-testing, both prompt-only fixes in `ResearchAgent`** (`src/openjarvis/agents/research_loop.py` — confirmed via the server route this IS the agent behind the web UI's "Deep Research" button, not `DeepResearchAgent`, which is a separate, already-correct, unused-by-that-button implementation — keep them separate per existing project rule):
    1. **No real-clock awareness** — the LLM had no grounding for the actual current date, so it guessed weekdays and got them wrong (confirmed: said August 21, 2026 — a real Friday — was a "Monday"). Fixed in two places: `src/openjarvis/prompt/builder.py`'s `SystemPromptBuilder.build()`/`sections()` now append a "Current Date and Time" section computed fresh on every call (deliberately kept OUT of the cached `_frozen_prefix`, which is cached for the builder instance's lifetime for prompt-cache stability — baking a date into that would go stale) — this covers `OrchestratorAgent` (the web chat's default agent) and `SimpleAgent`. `ResearchAgent.run()`'s `SYSTEM_PROMPT` previously passed only a bare ISO datetime with no weekday (`datetime.now().isoformat(timespec="minutes")`), forcing the LLM to compute the weekday itself — added a separate `{today_weekday}` placeholder (`now.strftime("%A")`) alongside the unchanged ISO `{today}` (which must stay pure ISO — the prompt's strategy rules have the LLM copy `{today}` verbatim into `time_range` JSON tool-call arguments, so its format can't change) and told the model to trust the stated weekday rather than recompute it.
    2. **Search filters incorrectly applied to evergreen reference content** — Deep Research returned 0 results for "what is my class schedule for today" even though the note was correctly indexed. Root cause confirmed directly against `HybridSearch.search()`: the LLM was passing `person="me"` (per the original strategy rule "if the user names a person, ALWAYS pass person=") for generic first-person phrasing like "my class schedule" — the note has empty `author`/`participants` metadata (it's a note, not a message with a sender), so that filter correctly-but-unhelpfully excluded it every time. Separately, the LLM also applied a `time_range` filter for "today" — but the note's stored timestamp is its *ingestion* time, not an "as-of" date, so a schedule note synced today would silently stop matching "today" the very next day. Both are prompt-guidance fixes in `research_loop.py`'s `SYSTEM_PROMPT` strategy section (rules 1 and 2): don't pass `person=` for self-referential "my/I/me" phrasing (only for a named other person), and don't pass `time_range=` when searching reference/informational content (notes, schedules, documents) — only for genuinely time-bound content (messages, emails, calendar events).
  - **Two Windows-environment issues found and fixed along the way, not code bugs**: (1) Windows' global notification toggle was off (`HKCU\Software\Microsoft\Windows\CurrentVersion\PushNotifications\ToastEnabled = 0`) — user turned it on, confirmed via two real on-screen toasts during testing; (2) the `Sage-ClassScheduleCheck` scheduled task flashed a visible console window every 5 minutes since `jarvis.exe` is a console-subsystem executable and Task Scheduler doesn't suppress that by default — fixed by pointing the task's action at a hidden VBScript wrapper (`C:\AI\OpenJarvis-Data\scripts\run_class_schedule_check_hidden.vbs`, `WScript.Shell.Run(..., 0, True)`, window style 0 = hidden) invoked via `wscript.exe //B`, instead of launching `jarvis.exe` directly. Confirmed silent via `Start-ScheduledTask`.
  - Drive-by fix: `CLAUDE.md`'s stale `OPENJARVIS_CODING_DIRS`/`FILE_READ_DIRS`/`FILE_WRITE_DIRS` note (claimed `C:\AI\Sage-Workspace`) corrected to the confirmed-live `C:\AI`.
- **Default local model switched to `qwen3.5:4b`** (from `qwen3.5:9b`) — both `C:\AI\OpenJarvis-Data\config.toml`'s `[intelligence] default_model` and `[memory] extraction_model`, plus the web UI's own default in `frontend/src/lib/store.ts` (`loadSettings()`'s `defaults.defaultModel`, with a fix so a stale empty string previously persisted in a browser's `localStorage` can't silently override the new default — only an explicit non-empty value can). `qwen3.5:4b` was pulled via `ollama pull` and confirmed present locally (`ollama list`). Not related to M26 — a separate, smaller request from earlier the same session.

- **Morning digest reachable via chat** — new `build_morning_digest_agent()` factory (`agents/morning_digest.py`) plus intent-detection routing in `server/routes.py::chat_completions` (same regex `QueryOrchestrator` already had, ported to the code path the real web UI actually calls). Typing "give me my morning digest" now works, live-verified. Along the way: fixed a `Path("")` false-positive-audio bug (`digest_store.py`, `morning_digest.py`, `cli/digest_cmd.py`, `server/digest_routes.py` — `audio_path` is now `Optional[Path]`), added an Obsidian "NOTES" digest section (recently-modified notes, `digest_collect.py` + `morning_digest.py`), and fixed a real `ObsidianConnector.sync(since=...)` bug (naive vs. tz-aware datetime comparison crashed — never triggered before since nothing had called it with a real path). `[digest]` section added to `config.toml` (`sections` includes `music`, `notes`).
- **Spotify token refresh fixed** — new `connectors/spotify_auth.py` (401→refresh→retry, mirrors `microsoft_auth.py` but uses Basic-auth for the refresh grant per Spotify's requirement). `SpotifyConnector` had no refresh logic at all before; access tokens expire hourly, so it broke permanently after the first hour post-connect. Live-verified: `jarvis connect --sync` now indexes real Spotify chunks instead of 401ing.
- **`scripts/restart-sage.ps1`** — kills whatever's on ports 8000/5173/5174 and relaunches backend + frontend in two fresh windows, one command instead of four.
- **Wake word ("Hey Sage") retrained via Colab, then fixed with a local speaker-verifier after the retrained model turned out not to generalize to real speech at all** (`167ebb3`). The Colab-trained classifier scored 0.87 accuracy / 0.74 recall / 0.0 FP-per-hour on synthetic validation data, but live-tested at ~0.0008 on real "Hey Sage" utterances through the actual mic pipeline — statistically identical to silence. Root cause never fully isolated (ruled out: feature-extractor version skew between train/inference, both pinned to the same v0.5.1 openWakeWord release assets) — most likely the auto-training pipeline's negative-example reweighting overcorrected toward "never fire" on anything outside its narrow synthetic TTS distribution. Rather than a full Colab re-run, trained a local `openwakeword.custom_verifier_model` (logistic regression on the shared embedding features, bypassing the broken classifier's decision layer entirely) from two real recordings (~13 "Hey Sage" utterances, ~25s of other speech) — wired into `wake_word.py`'s `WakeWordDetector` via `custom_verifier_models`/`custom_verifier_threshold=0.0` (the library's 0.1 default gate would never let the verifier get consulted, since the base classifier never scores that high). `DEFAULT_THRESHOLD=0.76` and a hand-rolled `DETECTION_PATIENCE=2` (consecutive-frame requirement) were calibrated against live score measurements through the real capture pipeline (ambient silence ~0.3, keyboard clicks ~0.6, genuine speech 0.74–1.0) — **do not use openWakeWord's own `predict(patience=...)`**: confirmed it checks its own already-patience-adjusted history buffer rather than raw scores, so the first frame (no history yet) always gets zeroed, which then poisons every later frame's lookback too, permanently, regardless of input. `useWakeWord.ts` also got: `noiseSuppression`/`autoGainControl` disabled (AGC was boosting keyboard clicks into the same score range as real speech — raw mic input matches training conditions better); reconnect-with-backoff for transient WebSocket drops (previously surfaced a scary toast and just went silent); a stale-closure fix so post-reconnect audio frames go to the *current* socket, not the one from initial setup; and a real React async-race fix — `start()`'s in-flight `getUserMedia()` await could resume after `stop()` already ran and nulled every ref, resurrecting an orphaned mic/socket session nothing could ever tear down again (fixed with a session-token guard, re-checked after every `await`). Live-verified via a screen recording after the async-race fix: no more mic-loop after a successful "Hey Sage" → command → voice reply cycle.
- **Voice-conversation self-sustaining loop, found and fixed** (`167ebb3`): a wake-word false-trigger recording (mostly silence) still produced a non-empty Whisper transcript — Whisper is known to hallucinate boilerplate text from silence rather than returning empty — which got sent as a real message, got a voice reply, and (with Continuous Conversation on) re-armed listening once that reply finished, sustaining the loop indefinitely. Fixed with `vad_filter=True` in `faster_whisper.py`'s `transcribe()` call (was never set — silence now correctly yields empty). Separately, `InputArea.tsx`'s wake-word listener now also gates on `!audioPlaying`, not just `speechState==='idle'`: `speechState` returns to idle right after transcription, well before a reply is even generated let alone spoken, so wake word was listening (and could pick up its own TTS reply through imperfect echo cancellation) during that whole window. `AudioPlayer.tsx` had a related mount-time race (seeds `playing` from `autoPlay` now instead of always `false`) and `store.ts` had an unrelated but adjacent bug where reopening a past/recent chat replayed a stale `audio.autoPlay=true` flag, auto-playing old audio on every load (`withoutAutoPlay()`, applied at all four message-hydration sites).
- **General voice-reply TTS (M27 continued — see line above on the first piece)**: voice-originated turns now get *any* reply spoken back, not just ones that happen to carry digest audio. New `ChatCompletionRequest.voice: bool` / `ChatRequest.voice?: boolean`, sent as `voice: wasVoice`. `routes.py::_handle_agent()` calls TTS directly (not via model tool-call — same reasoning already applied to `notify_class_schedule`: don't trust a small model to remember to call a tool every time) when a voice-originated reply didn't already get audio from a tool, reusing the existing `TextToSpeechTool` + `_SYNTHESIZED_AUDIO`/`GET /v1/speech/audio/{token}` plumbing (found already built for the manual-synthesize endpoint) rather than building a parallel mechanism. Strips markdown first so TTS doesn't read literal `**`/`#` aloud. Costs a real Cartesia call per voice turn now, not just digests — user is aware and subscribing to Cartesia Pro.
- **Date/weekday hallucination, partially mitigated** (`167ebb3`): `SystemPromptBuilder` already injected the real current date (see M26's date-awareness fix above), but live-testing still caught the model writing "Friday, August 22, 2026" in the same response where it had *just correctly written* "Saturday, August 22, 2026" moments earlier — conflating a schedule note's recurring "Day" column with "today" even with the correct date in hand. Reworded `_current_datetime_content()` to name the two concepts as distinct, and added a second short reminder as the prompt's final section (`datetime_reminder`, a few tokens) — the first mention can be tens of thousands of tokens back in a large context (one live case measured 45,390 input tokens) and models attend most reliably to the very end of a long prompt, right before the actual question. `build()` now derives from `sections()` so the two can never drift apart (a pre-existing test enforces this invariant). **This is a same-response reasoning-consistency failure, not a missing-data one — the model has the right date and still contradicts itself; expect it to still happen sometimes on the local `qwen3.5:4b` model.** Next planned step (not yet done): re-test the same date-sensitive query on a cloud model instead, which should not have this failure mode.
- **Critical infra finding: `jarvis serve` was silently running a 3-day-stale frontend the entire troubleshooting session.** `jarvis serve` serves a pre-built static bundle from `src/openjarvis/server/static/` (populated by `npm run build` in `frontend/`, `outDir` set in `vite.config.ts`) — restarting the backend does **not** pick up frontend source changes; the bundle must be rebuilt. Several rounds of live-tested-and-reported-as-still-broken frontend fixes this session turned out to have never actually been running, because the served bundle predated every one of them by 3 days. There's also a PWA service worker (`sw.js` via `vite-plugin-pwa`, `generateSW` mode) that can hold an even-staler cached bundle independent of server-side no-cache headers — a hard refresh or full tab close/reopen may be needed after rebuilding even once the server has fresh files. **Any future frontend change must be followed by `cd frontend && npm run build` before testing against `jarvis serve`, not just a backend restart.**
- **Stale scheduler-task cleanup** (outside this repo, `C:\AI\OpenJarvis-Data\scheduler.db`): diagnosed a Saturday false-alarm Windows toast as a stale/queued notification from two already-cancelled scheduled tasks (`f2380e72d8b54599`, `2561830006564818`) that used the old model-decides-whether-to-notify pattern the M26 `notify_class_schedule` tool was built to replace — confirmed via `scheduler.db`'s `task_run_logs` table that the *current* active task correctly returned "nothing upcoming" on every Saturday run, and that neither cancelled task had run since Friday. Deleted both dead rows directly from `scheduler.db` (`DELETE FROM scheduled_tasks WHERE id IN (...)`) — cosmetic cleanup, not a behavior change, since cancelled tasks don't run anyway.
- **Debug readout removed**: the mic-level/wake-word-score/msg-count row added to `InputArea.tsx` during this session's wake-word troubleshooting was removed once detection was verified reliable — it was popping in and out of the chat UI on every turn, wake-word-triggered or not.

## Current Git state

- **Cartesia voice fixed end-to-end** (`80bd5bc`): `"sonic"` model was sunsetted, switched to `"sonic-3"`; `jarvis digest`'s audio playback only knew Linux/macOS players, added a Windows `os.startfile` fallback; fixed the audio player showing up on unrelated messages (was polling `/api/digest` after every response instead of checking whether *this* response actually produced audio — now rides the chat stream's own finish event).
- **Speech-to-text (mic input) confirmed working** — real bug found and fixed, config-only (not in this repo, `C:\AI\OpenJarvis-Data\config.toml`'s new `[speech]` section): `faster-whisper` defaulted to `device="auto"` which resolved to CUDA, and this machine is missing `cublas64_12.dll` (a CUDA runtime lib separate from whatever Ollama bundles) — every transcription 500'd. Forced `device="cpu"`, `compute_type="int8"`; confirmed fast enough for short voice commands, live-verified via the actual web UI mic.
- **Real Jarvis-style voice conversation (M27 first piece): auto-speak replies to voice-initiated messages only, no manual play needed** — live-verified end-to-end. New `POST /v1/speech/synthesize` + `GET /v1/speech/audio/{token}` in `server/api_routes.py` (reuses the existing `TextToSpeechTool`/Cartesia, in-memory token→path map, no DB table — ephemeral voice-reply clips don't need to survive a restart). `AudioPlayer.tsx` gained an `autoPlay` prop (it never autoplayed before at all) and a `label` prop (was hardcoded "Morning Digest"). `InputArea.tsx` tracks a `voiceOriginatedRef` — set on a successful mic transcription, cleared the moment the user edits the textarea by hand — snapshotted as `wasVoice` at send time: if the reply already carries digest audio, that gets reused with `autoPlay: wasVoice` (no duplicate TTS cost); otherwise, only when `wasVoice`, a fire-and-forget call to the new synthesize endpoint patches in a second, auto-playing audio player once ready. Typed messages never trigger any of this, by design — confirmed live: "hi" (typed) → no audio; a voice question → auto-playing "Voice Reply" player; typed digest request → player present but not auto-played; voice digest request → auto-plays the real digest audio.

- Repo: `C:\AI\OpenJarvis-Lab`
- Branch: `feature/sage-customization`
- Latest HEAD is the commit containing this handoff, sentence-buffered incremental TTS, streamed-agent rendering/usage repairs, and atomic tool-history trimming; use `git log -1 --oneline` for its exact hash.
- Parent before this commit: `b00b7cd3` (`fix: sanitize spoken replies and sync orb playback`).
- `origin/feature/sage-customization` remains at `3f55362f`; after this commit the local branch is **ahead by 4**. Nothing from this slice has been pushed; do not push without explicit approval.
- The working tree and index are expected to be clean after this commit. The 25 committed files are this handoff plus the 24 incremental-TTS, streaming-render, history-repair, and regression-test files listed by the commit.
- `C:\AI\OpenJarvis-Data\config.toml`, `C:\AI\Sage-Vault\Class Schedule.md`, `C:\AI\OpenJarvis-Data\scheduler.db`, and the Windows Task Scheduler entry / VBScript wrapper are all outside this repo's Git tree — never part of any commit, tracked here in HANDOFF.md instead. The Colab notebook used to retrain the wake-word model, and the trained model files themselves (`C:\AI\Hey_Sage.onnx`, `C:\AI\Hey_Sage_verifier.pkl`, backup at `C:\AI\Hey_Sage.onnx.bak-20260822-141101`), are likewise outside this repo.

## Verification

- Incremental TTS/stream-render slice (2026-08-31): focused backend suite **141 passed** and frontend suite **176 passed**; focused Ruff, frontend lint/build, and production bundle build passed. Regression coverage includes speech beginning before model completion, ordered sentence delivery/final-tail flush, secret-like material split across deltas, unchanged chat/history, Stop/context cancellation and late-frame rejection, pre-audio fallback versus post-audio no-replay, queue bounds, disconnect cleanup, truthful orb state, usage counters, and adaptive long-answer rendering.
- Long-history tool replay repair (2026-08-31): both new regressions were confirmed failing on the unfixed code. Focused loop-guard/message-building suite **32 passed**; broader `test_loop_guard.py`, `test_base_agent.py`, `test_orchestrator.py`, and `test_routes.py` run had **138 passed** with only the documented pre-existing `TestToolUsingAgent::test_default_max_turns` assertion failing (`15` configured versus stale expected `10`). Ruff and `git diff --check` passed on the repair. Sage restarted cleanly, `/health` returned `ok`, and a live `gpt-5.6-luna` request carrying a long assistant answer plus historical tool call/result completed HTTP 200 with `[DONE]`, assistant text, and no invalid-tool-history error.
- Speech-only sanitization/orb lifecycle slice (2026-08-31): focused Python speech/TTS/server/digest suite **70 passed**; full frontend suite **164 passed**; Ruff clean on the four touched Python implementation/test files; production frontend build succeeded; `git diff --check` clean. Sanitization benchmark was approximately **0.547 ms per 5,000-character transform** over 1,000 runs. Live Sage served the refreshed `/assets/index-DPUpgOoj.js` bundle, backend health returned `ok`, the browser reported no errors, and the user accepted the observed result. The deterministic tests cover playback ownership, stale-generation rejection, and base-plus-output-latency tail handling; an additional microphone permission prompt was deliberately not forced during final browser inspection.
- Focused Gmail, checkpoint, config, OAuth, connector API, and Deep Research suite: **130 passed** (prior session).
- `tests/cli/test_connect.py`: **12 passed** (9 prior + 3 new for `--sync`), Ruff clean on `connect_cmd.py` and the test file.
- Broader pass — `tests/cli/ tests/connectors/test_obsidian.py tests/connectors/test_gmail.py tests/connectors/test_sync_engine.py tests/core/test_config.py`: **591 passed**, 1 failed (`tests/cli/test_scan.py::TestRunQuick::test_run_quick_returns_subset` — pre-existing Windows-vs-Linux platform-detection assumption, confirmed unrelated to any change here).
- A wider `tests/cli/ tests/connectors/` pass separately surfaced 5 more failures, all confirmed pre-existing/environmental, not caused by this work: a Windows SQLite-file-lock-on-teardown issue in `test_live_smoke.py::test_live_obsidian_full_pipeline` (the actual smoke test passed — "SMOKE TEST PASSED, 6466 chunks indexed" — only fixture cleanup failed), and four `test_new_connectors_live.py` tests (Oura/Strava/Spotify/Google Tasks) that need real credentials not configured on this machine.
- Live `jarvis connect --sync`: synced exactly `gmail` and `obsidian` (0 new chunks each — both already fully ingested, confirming incremental/checkpoint behavior), left `gcalendar`/`gdrive`/`gcontacts`/`google_tasks` untouched despite valid OAuth tokens for all four.
- Live `jarvis connect --list`: unchanged behavior, still correctly shows credential status (`gdrive`/`gcontacts` show "connected" — that's accurate, they have valid tokens from the shared OAuth grant — this is a different, intentionally separate concept from "will `--sync` touch it").
- The full consolidated suite was NOT rerun this session (per the `AGENTS.md` rule — not a release/milestone boundary/broad shared-core change).
- Outlook/Microsoft focused suite (`test_outlook.py` + `test_microsoft_auth.py` + `test_oauth_microsoft.py` + `test_config.py`): **89 passed**, Ruff clean.
- Broader regression `tests/connectors/ tests/cli/test_connect.py tests/core/test_config.py`: **449 passed**, 5 failed — all 5 confirmed pre-existing/environmental (the same Windows SQLite-teardown issue in `test_live_smoke.py` and 4 `test_new_connectors_live.py` tests needing real Oura/Strava/Spotify/Google-Tasks credentials not present on this machine), none caused by the Outlook work.
- Live CLI: `jarvis connect --list` correctly shows `outlook | oauth | disconnected` (was IMAP-flavored before this rewrite). `jarvis connect outlook` run non-interactively reached the "no client credentials found" branch and printed the Azure setup URL/hint correctly before failing at the interactive prompt (expected, no terminal to type into). Run for real by the user afterward: reached Microsoft's consent screen correctly but stopped at NU's admin-consent wall (see "Verified completed work" for details) — confirms the OAuth wiring itself is correct up to a real external tenant policy, not a bug in this codebase.
- Live CLI: `jarvis connect spotify` completed successfully — `_activated.json` and `spotify.json` both confirm a real, valid connection (subsequently broke with a 401 on refresh — see above, deferred).
- `tools/` suite after M26's two new tools + description-tightening fix: **696 passed, 4 skipped** (pre-existing skips), Ruff clean.
- `tests/prompt/ tests/agents/ tests/server/ tests/cli/test_serve* tests/sdk/` after the date-awareness + search-filter fixes: **1027 passed, 5 failed** — all 5 confirmed pre-existing/environmental/flaky (checkpoint-ID formatting, npm runner-dir setup, timing-precision on `total_latency_seconds`, and the same `OPENJARVIS_HOME`-driven persona-path test seen elsewhere), none caused by this work. Two real regressions were found and fixed in the same pass: `test_executor_tools.py`'s two exact-system-prompt-content assertions (`== "SENTINEL"`) needed to become `.startswith("SENTINEL")` since `SystemPromptBuilder.build()` now always appends a trailing date section.
- M26 live end-to-end verification (not just unit tests): manually added a synthetic test row to `Class Schedule.md`, ran `jarvis scheduler run-task orchestrator` for real — confirmed the orchestrator called `check_class_schedule` then `notify_windows`, a real Windows toast appeared (user-confirmed on screen), a second immediate run correctly sent nothing (dedup state worked), test row removed afterward. Windows Task Scheduler entry created and manually fired via `Start-ScheduledTask` — confirmed silent (no console flash) after the VBScript-wrapper fix.
- Deep Research fix live-verified by the user directly: "what is my class schedule for today" on the web UI's Deep Research now correctly finds and returns the note's content (previously 0 results across 3 search attempts).
- Wake-word/voice-conversation session (`167ebb3`): **141 passed** across `tests/prompt/`, `tests/server/test_routes.py`, `tests/server/test_speech_routes.py`, `tests/server/test_api_routes.py`, `tests/speech/`, `tests/tools/test_check_class_schedule.py`; 1 failed (`tests/prompt/test_persona_scope.py::test_named_persona_resolves_to_personas_dir` — pre-existing, this machine's `OPENJARVIS_DATA`/`get_config_dir()` resolves to `C:\AI\OpenJarvis-Data` rather than the test's hardcoded `~/.openjarvis` assumption, confirmed unrelated by checking it fails identically without any of this session's changes applied). Frontend `tsc --noEmit`: clean. Ruff: clean on every file this session touched (pre-existing E501s remain on 3 files in code this session didn't author — `routes.py:102`, `faster_whisper.py:38/40` (CUDA DLL setup, predates this session), `wake_word.py:35` (predates this session)). Live end-to-end: verified via a user-recorded screen capture (extracted frames + transcript via PyAV/faster-whisper to inspect it, since the Read tool can't open video directly) that the async-race mic-loop fix actually resolved the reported symptom in a real "Hey Sage" → command → voice reply cycle.
- One real regression caught by a pre-existing test and fixed in the same session: `tests/prompt/test_builder.py::test_sections_expose_prompt_metadata` enforces `build() == "\n\n".join(sections())` — the first pass at the date-hallucination fix appended a string directly in `build()` without a matching `sections()` entry, breaking that invariant. Fixed by making `build()` derive from `sections()` and adding the reminder as a proper `datetime_reminder` `PromptSection`; the test's expected section-name list was updated to include it.

## Constraints

Preserve fail-closed `OPENJARVIS_*_DIRS`, explicit Git file lists, sensitive-file blocks, OAuth scopes/credentials/checkpoints, Tavily-only search, and the two separate research-agent implementations. Never request, display, log, or persist secrets. Prefer recoverable operations with logging. Sage requires restart after code or config changes. Web impact must be classified for every change. **New**: never assume a source's OAuth token being valid (`is_connected() == True`) means the user wants it synced — only `connectors/_activated.json` (written by an explicit `jarvis connect <source>` run) authorizes `--sync` to touch it. Tests that exercise `_connect_source`/`_sync_all` real code paths must mock `openjarvis.core.config.DEFAULT_CONFIG_DIR` to a `tmp_path` — omitting this writes real state to the user's actual `C:\AI\OpenJarvis-Data\connectors\` directory (this happened once already, see "Verified completed work"). **New from M26**: `SystemPromptBuilder`'s `_frozen_prefix` is cached for the builder instance's lifetime for prompt-cache stability — anything that must be correct per-request (like the current date) must be computed in `build()`/`sections()` outside that cache, never inside `_build_frozen_prefix()`. `jarvis scheduler run-task <agent>` does not pass `tools=` to `system.ask()` — any tool a scheduled task needs must be in the agent's globally configured tool list (`config.toml`'s `[agent] tools`), not assumed to come from the task's own `--tools` flag (which is currently inert, stored but unread by that code path). `ResearchAgent`'s `person=`/`time_range=` search filters are for message/time-bound content only — do not let the LLM apply them to reference/informational content (notes, schedules, documents) that has no author/participant metadata and no meaningful "as-of" date. **New from the wake-word session**: `jarvis serve` serves a pre-built frontend bundle — a backend restart alone never picks up frontend changes, `cd frontend && npm run build` must run first, and a hard refresh (or closing/reopening the tab) may still be needed after that because of the PWA service worker's own independent cache. Do not use openWakeWord's `Model.predict(patience=..., threshold=...)` — it has a confirmed self-referential deadlock bug (see "Verified completed work"); use a hand-rolled consecutive-frame counter instead, tracking raw scores independently of the library's own history buffer. Any `async` React hook effect that can be re-triggered mid-flight (mic/audio setup, socket connect) needs a session-token guard re-checked after every `await`, not just an `enabled`-prop dependency array — `useWakeWord.ts`'s `start()`/`stop()` is the reference pattern.

## M28 in progress — two known-open issues (2026-08-23)

First slice of M28 (open a known desktop app, then drive it through its own API rather than clicking in it) is built and live-verified: `tools/open_app.py` (allowlist-only launcher) and `tools/spotify_control.py` (play/pause/next/previous, song search). Both are in `config.toml`'s `[agent] tools`. Six commits, `cbf4cef` through `1ab7118`. Playing music, opening Spotify, and opening Notepad all work end to end.

Spotify needed new OAuth scopes (`user-modify-playback-state`, `user-read-playback-state`). **`jarvis connect spotify` cannot grant them** — it skips the OAuth flow whenever a token file exists and only re-syncs history. Use `scripts/reauth-spotify.py`, which calls `run_connector_oauth` directly and preserves the stored client credentials.

Three real bugs found only by live testing, all fixed and regression-tested (`tests/tools/test_spotify_control.py`): Spotify keeps a Connect device registered server-side after the client exits, so the API reports a phantom active device and playback "succeeds" with no window and no audio — liveness must come from the local process, never the device list; device choice must prefer the local hostname or playback lands on another computer on the account; and `search?limit=1` can return an empty page for a query that plainly matches, so a findable song reads as "not found" (now 5).

**Both items below were resolved in a later session (see "M28 completed" further down) — left here unedited as the historical record of what M28 shipped with initially.**

1. ~~Launched windows stay minimised in the taskbar.~~ Fixed: `open_app._raise_window()` switched from `SetForegroundWindow` (Windows refuses it from a background process, so it never actually worked) to a `SetWindowPos` topmost/no-topmost toggle, which doesn't require foreground permission at all.

2. ~~History mimicry is reduced but not eliminated.~~ Traced to a real, unrelated bug: `LoopGuard`'s identical-call counters live on the agent instance, which is built once at server startup and reused for the process's entire lifetime — so `max_identical_calls=3` meant the *third* "open obsidian" of the server's whole uptime got refused forever after, not just within one conversation. Fixed by resetting the guard at the start of every `agent.run()`.

## M28 completed, M31 in progress (2026-08-23 to 2026-08-26)

**M28 (Spotify-style app launch + playback control) shipped.** Both items above fixed. `open_app`/`spotify_control` hardened through roughly a dozen live-testing rounds: phantom Spotify Connect devices, wrong-device playback targeting, `open_app` being called redundantly before `spotify_control` (tool description rewritten so the model stops doing this), the LoopGuard permanent-block bug above, "already paused" being reported as a failure (403 "Restriction violated" is Spotify's overload for "harmless no-op", not an error), and extensive window z-order work.

**Wake word ("Hey Sage") went through a second full round of debugging and retraining, now at 100% held-out recall.** The Colab-trained verifier from the entry above eventually needed replacing outright — a mic upgrade meant the old training recordings no longer matched the user's actual voice/hardware. In order:
- **Root-caused and fixed a false-refresh-trigger bug**: the wake-word detector is shared across WebSocket sessions and scores a rolling window; a freshly-reset detector's score spikes right as that window first fills, *regardless of audio content* — confirmed via the user's own quiet-room negative recordings, every one of which peaked at that exact point. Every page refresh (fresh socket → fresh detector) was firing on nothing but room noise. Fixed with a `WARMUP_FRAMES` grace period (`wake_word.py`) plus giving each WebSocket connection its own detector via `WakeWordDetector.clone()` (previously shared one instance, so a closing connection's in-flight frames could bleed into a new session's buffer).
- **Found and fixed a training-time version of the same bug**: the feature-extraction script was capturing "positive" training examples right at that same buffer-fill transient (most recording takes start immediately, so the word landed there too) — so the verifier had learned "a buffer that just filled" instead of the word itself. `scripts/wake_word_train.py`'s extraction now pads with lead-in noise so no capture, in either class, ever lands on the transient.
- **Built `scripts/wake_word_autolabel.py`**: segments one long natural-talking recording by frame energy and auto-labels each burst via the project's own faster-whisper backend, matching against "hey" + a fuzzy sage-like word. Caught a real, serious bug of its own: a session recorded as "say only Hey Sage" had 15 of the user's genuine attempts filed as *negatives* because Whisper misheard them ("ACG.", "He sings.", "Hey, guys.") — the auto-labeler was training the model to reject the user's own pronunciation. Relabelling by hand lifted held-out recall from ~81% to ~96% with no new recording. Lesson recorded in the script's own comments: an unrecognisable transcript in a single-phrase session means the recognizer failed, not that the user said something else — don't trust the transcript blindly in that context.
- **Final round used a purpose-built guided recording page** (`frontend/public/wake-word-trainer.html?mode=guided`) instead of auto-labeling: 47 explicitly-labeled takes (30 "Hey Sage" spread across normal/fast/quiet/far/loud delivery, 17 negatives — typing, mouse, room tone, ordinary sentences — recorded at the *same* mic level as the positives). This removed the auto-labeling mislabeling risk entirely and, more importantly, removed a loudness shortcut: the positives collected so far had all been loud (45–75% peak) and the negatives all quiet (2–8%), so the classifier could partly succeed by learning "loud = trigger" rather than the word. An ablation test confirmed a related idea (adding "hey there"/"hey guys" near-miss negatives) changed nothing — training with vs. without all 14 existing ones gave identical results — so the guided script skips those entirely.
- **Result, held out on the user's three most recent recording sessions** (156 positive / 79 negative clips total, `Hey_Sage_verifier.pkl` retrained from all of them): **100% recall at every threshold from 0.71 to 0.83**, with false-fire rate improving as threshold rises. Deployed at `DEFAULT_THRESHOLD = 0.79` for margin — recall no longer needs it lower. The 9 intermediate `.bak` verifier files and 1 `.onnx.bak` from this iteration were deleted 2026-08-26; only the current live `Hey_Sage.onnx`/`Hey_Sage_verifier.pkl` remain in `C:\AI`.
- Fixed a real duplicate-trigger bug found only after the model got good: one spoken "Hey Sage" produced up to 8 detections server-side (the rolling window stays above threshold for several consecutive frames, and every one counted separately) — invisible before because `speechState` used to flip to `'recording'` within milliseconds and disarm the wake word; a later UX change (below) held it armed for ~1.4s and exposed it. Fixed by resetting the detector after every reported detection (`api_routes.py`) plus a client-side re-entrancy ref (`InputArea.tsx`).

**New feature: spoken acknowledgement on wake-word trigger (M31-adjacent, user-requested).** "Hey Sage" now gets an immediate "Hello, sir" (one of 3 rotating variants) before listening starts, so a trigger is audible. Clips are pre-rendered once via `scripts/generate_greetings.py` (real Cartesia TTS in Sage's voice) into `frontend/public/greetings/`, not synthesized per-trigger — avoids a network round trip at exactly the moment latency matters. Went through two design iterations: first an overlapping/barge-in version (capture starts immediately, greeting cuts short the moment the user is heard) was built, but the user explicitly preferred reliability over speed after the barge-in cancelled the greeting on the tail of their own "Hey Sage" and it never played — reverted to strictly sequential (greeting finishes, *then* capture starts; the mic is still opened early so there's no device-init lag). New "Greet on Wake Word" setting (`store.ts`/`SettingsPage.tsx`, default on) mutes it, primarily so recording training data doesn't fight the app.

**Spotify: two more real bugs found and fixed, live-verified against the real API.**
- `"next song"` etc. were being hallucinated by the model — confirmed by checking actual Spotify playback state before/after a "next" request: track never changed even though the model replied "Skipped to the next track." with high confidence. A system-prompt warning against exactly this did not change the behaviour. Routed deterministically instead (`_SPOTIFY_TRANSPORT_RE` in `routes.py`, same technique as the existing `_DIGEST_INTENT_RE`), bypassing the model's tool-choice for next/pause/previous/skip entirely.
- Separately, `"next"` had *nothing to skip to* even when actually called: playing a named song sent `{"uris": [one_uri]}`, which gives Spotify a queue exactly one track long. Fixed by starting the track's album as playback context (`context_uri` + `offset`) instead. Verified live: "Life Puzzle" → next → "Higa".
- Bare "play a song" always replayed the single most-recent track. Re-authed with two new scopes (`user-top-read`, `user-library-read` — `scripts/reauth-spotify.py`, `connectors/oauth.py`) and changed `_any_familiar_track()` to pick randomly from a wide, de-duplicated pool of the user's real top tracks/saved tracks, falling back to recently-played only if those scopes are ever unavailable.

**HEAD as of this write-up: `eae01e5`** (`fix: raise wake-word threshold back to 0.79 now recall no longer needs 0.71`), 12 commits this session starting from `a6029d2`. Working tree clean except `frontend/tsconfig.tsbuildinfo` (build artifact, deliberately never staged). **42 commits ahead of `origin/feature/sage-customization`, not pushed** — same as every prior entry in this file, confirm with the user before pushing.

Tests: 421 passed across `tests/server/ tests/speech/` after the Spotify-transport-routing + wake-word-detector-reset changes; 40 passed in `tests/speech/` alone after the final threshold change. Frontend: `tsc --noEmit` clean after every change; `vitest run` (39 tests) clean after the greeting/barge-in/sequential-flow work. Ruff clean on every file touched this session (pre-existing E501s on lines this session didn't author are left as-is, same policy as every prior entry).

**Nothing outstanding as of that session** (superseded — see the section below for current state). No open bugs from it, no uncommitted source changes. If picking this up cold: the wake-word verifier and threshold are both live and tested; don't re-tune the threshold without a held-out check first (this session found the "obvious" lower-threshold trades were sometimes free and sometimes not — always measure, per the pattern in `wake_word.py`'s `DEFAULT_THRESHOLD` comment).

## Scheduler brought to life, cloud models made usable (2026-08-26 to 2026-08-27)

**HEAD `6258544`, 14 commits from `02bd938`. Everything is pushed** — `origin/feature/sage-customization` matches HEAD, the first time this file has been able to say that. Working tree clean except `frontend/tsconfig.tsbuildinfo` (build artifact, never staged).

### The scheduler was dead code, and turning it on exposed two more bugs beneath it

`openjarvis.scheduler.TaskScheduler` had never run. `SystemBuilder._setup_scheduler` built it **without a `system=`**, so `_execute_task` failed its `if self._system is not None` check and every due task logged `"[dry-run] Would execute..."` instead of running; nothing called `.start()` either. `[scheduler]` was also absent from `config.toml` and defaults to `enabled = False`, so `system.scheduler` was `None` outright. M26's class reminder only ever worked because it was routed around all of this via a Windows Task Scheduler entry.

Fixed in `02bd938`: added `TaskScheduler.set_system()` (mirroring `AgentExecutor`'s deferred injection — the scheduler is built before `JarvisSystem` exists) and called it from `SystemBuilder`. **`jarvis serve` needed separate wiring**: it deliberately avoids `SystemBuilder.build()` to dodge a ~30-40s double build (#263) and hand-assembles a `JarvisSystem` inline, so the task scheduler reuses that one. Its console line is labelled `Tasks:` rather than `Scheduler:` because `AgentScheduler` — a *different* system, ticking managed agents from `agents.db` — already owns that label.

Two latent bugs surfaced only once the loop actually ran:
- `9f40224` — `_execute_task` passed `system.ask()`'s return straight to `log_run`, but `ask()` returns a **dict** for tool-calling agents, so SQLite raised `Error binding parameter 5: type 'dict' is not supported` on every real execution. `jarvis scheduler run-task` already `json.dumps`'d dicts; the same coercion now applies to both paths.
- `434b5c5` — tasks created from chat defaulted to `agent="simple"`, and `SimpleAgent` is single-turn and **cannot call tools at all**, so a scheduled "check X and notify me" produced text and did nothing. This is the identical trap M26 hit. Default is now `orchestrator`.

**`Sage-ClassScheduleCheck` in Windows Task Scheduler is now DISABLED** (`Disable-ScheduledTask`, reversible) — the in-process poller supersedes it, and running both double-fired every 5 minutes.

**Three separate scheduling systems exist and are easy to conflate.** (1) the fixed `orchestrator` agent handling interactive chat, no scheduler involved; (2) the "Agents" web UI page — `agents.db`, `AgentManager`, ticked by `agents/scheduler.py`'s **`AgentScheduler`**, alive since long before this session; (3) TOML-manifest Operators via `operators/manager.py`'s **`OperatorManager`** — a genuinely different thing from `AgentManager` despite the name — which calls `self._system.scheduler` and so was dead for exactly the same reason the CLI tasks were. Fixing `TaskScheduler` revived both (3) and the CLI tasks at once.

### Sage can now schedule its own work from chat

`584d1be` wired up the five scheduler tools in `scheduler/tools.py` (`schedule_task`, `list_scheduled_tasks`, `pause_`/`resume_`/`cancel_scheduled_task`). They were **dead three ways over**: the module was never imported so `@ToolRegistry.register` never ran; nothing set the `_scheduler` class attribute so `execute()` would have returned "Scheduler not available"; and the names weren't in `config.toml`'s `[agent] tools`. Reasonable, since anything they scheduled couldn't have run before the poll loop started. `set_scheduler()` injects **on the classes**, because the registry builds tools with no arguments far earlier in `serve.py` than the scheduler is created, and `execute()` reads `self._scheduler` at call time.

**Everything the model would otherwise have to compute is now computed in Python** — the recurring lesson of these two days:
- **Cron is evaluated against UTC** (`_compute_next_run` uses `datetime.now(timezone.utc)`). This machine is **UTC+8**, so "every morning at 8" would have fired at 16:00 local. `_local_cron_to_utc` shifts the hour and carries day-of-week across a UTC boundary (`0 5 * * 1` Mon-local → `0 21 * * 0` Sun-UTC); step/range hours and day-of-month pins are **left alone with a note** rather than shifted wrongly.
- `b944bbf` — `once` schedules had the same hole. `SchedulerStore.get_due_tasks` compares `next_run` to a UTC-aware ISO string **as text**, so a naive local timestamp is silently read as UTC and fires 8 hours late. Now normalised to an explicit UTC instant.
- `226517f` — asked to list tasks, the model got **all three** timings wrong: `0 0 * * *` → "daily at midnight" (it's 08:00 here), `600` → "every hour", a tomorrow-08:00 one-off → "today at noon". `list_scheduled_tasks` now returns `schedule_human`, `next_run_local` and `last_run_local`, and its description tells the caller to quote them.
- `a9ada46` — new **`world_time`** tool. Asked the time in Japan, the model reported the local clock correctly and then claimed UTC+9 is "3 hours ahead" of UTC+8. It already receives the real current time from `SystemPromptBuilder`; it had the data and failed the arithmetic, so more context would not have helped. `zoneinfo` resolves the zone (legacy aliases like `Japan`, bare cities like `Tokyo` via a last-segment index, plus ~45 countries that are neither, e.g. `Philippines` → `Asia/Manila`), Python does the subtraction, and the tool returns a finished `summary`. Fractional offsets are preserved — India UTC+5:30, Nepal UTC+5:45.

### Cloud models are reachable from Deep Research and from scheduled work

- `2f6ef66` — Deep Research resolved **engine and model independently**, so picking a cloud model set the model while the engine stayed on the active chat engine. The existing mismatch guard could not catch it because **`can_serve()` defaults to `True` for local engines** (`engine/_stubs.py` — whether a model is *installed* is deliberately a separate concern from engine selection), so `ollama.can_serve('gpt-5.6-luna')` returns `True`. Now the model resolves first and picks the engine, behind an explicit `[deep_research] engine` override. New public `is_cloud_model()` in `engine/cloud.py`.
- `58a5705` — with routing fixed, the first live run died on `TypeError: Completions.create() got an unexpected keyword argument 'num_ctx'`. `ResearchAgent` is engine-agnostic and passes Ollama's `num_ctx` on every `generate()`; every provider SDK rejects it. Local-only params are now stripped at the CloudEngine boundary.
- `0bb493b` — `JarvisSystem.ask()` took **no model at all**, so every scheduled task ran on whatever the server booted with. `ask(model=...)` now resolves an engine via `_engine_for_model()`, asking the active engine's `list_models()` first (it is usually a `MultiEngine` routing cloud models by prefix) and only resolving the cloud engine when the name isn't served. Scheduled tasks carry the model in **`metadata`, not a new column**: `scheduled_tasks` is created with `CREATE TABLE IF NOT EXISTS` and has **no migration path anywhere**, so a column would silently skip existing databases. Results now report the engine and model that actually served the call.
- `3a832ee` — `[proactive] model`/`engine`, applied before `BaseAgent` stores them.

**Only OpenAI is configured** (`cloud-keys.env`), so `gpt-*`/`o3-*` work and `claude-*`/`gemini-*` correctly fail with "cloud cannot serve model". Adding a provider is just its key.

### A small model will paraphrase any free-text field you give it

Both caught by testing real phrasings rather than assuming:
- `8a604b3` — "using gpt luna" became **`"gpt-luna"`** in the tool call. Not a real id; `is_cloud_model()` still returns `True` for it (any `gpt-` prefix), so routing worked and only the call would have 404'd, unattended, months later. `_resolve_model()` now validates against `CloudEngine.list_models()`; a paraphrase matching exactly one model resolves with a note, and anything matching several or none **fails the call** (`gpt-mini` names both `gpt-4o-mini` and `gpt-5-mini`, so it is refused, not guessed).
- `6258544` — the same confusion then appeared one field over: **`agent="gpt-5.6-luna"`** with `model` empty, which would have returned "Unknown agent" when it fired. A model id in `agent` is now moved to `model`; any other unknown agent fails and lists the real ones. An **empty `AgentRegistry` is treated as "can't tell" and allowed**, since `conftest` clears it and rejecting every agent because the registry hadn't loaded would be worse than the bug.

### Also fixed

- `d1c49c7` — **ProactiveAgent's descriptions did not match its payloads.** A read-only dry run against the real inbox had `qwen3.5:4b` propose "Delete Wells Fargo marketing email" (a subject copied from this agent's own few-shot example, matching no real message) pointing at the Cartesia mail, and separately name the Cartesia mail while targeting the Microsoft one. The Bell UI renders `description`, so approving one thing would have destroyed another — defeating the approval queue entirely, with `gmail.modify` scope and a real `conn.delete_message()` behind it. Descriptions are now rebuilt from the digest line the `doc_id` actually resolves to, unknown ids and duplicates dropped.
- **The "San Jose" location confusion was a bad stored fact, not missing location data** (outside this repo). "San Jose" is part of the user's *surname*; the memory extractor filed it as a place, and `memory_facts.jsonl` literally contained `{"text": "Location: San Jose", "source": "auto"}`, re-injected every turn — so conversational corrections could never win. Removed, and authoritative identity/location/timezone written into `OpenJarvis-Data/USER.md`, which is hand-maintained and not overwritten by extraction. **When Sage repeats a wrong "fact" after being corrected, grep `memory_facts.jsonl` first.**

### Verification

Tests: **860 passed, 4 skipped** across `tests/scheduler/ tests/system/ tests/tools/ tests/agents/test_proactive_agent.py`; 1166 passed on a wider `ask()`-consumer run (`tests/server/`, `tests/sdk/`, `tests/operators/`). Ruff clean on every file touched. Pre-existing failures left alone and confirmed identical on a stashed clean tree: `test_base_agent.py::test_default_max_turns`, two `test_claude_code.py::TestEnsureRunner` cases, `test_manager.py::TestCheckpoints::test_checkpoint_retention_max_5`, and four `TestGemmaCppLive` cases needing Kaggle weights.

New `tests/system/` package. **Two test-isolation traps worth knowing**: `tests/conftest.py`'s autouse `_clean_registries` clears `ToolRegistry` *and* `AgentRegistry` before every test, so an in-process import cannot prove registration (modules are cached, decorators don't re-run) — use a subprocess; `importlib.reload` "works" but rebinds the tool classes and silently breaks other tests in the same file. And any test passing `model=` must mock `openjarvis.scheduler.tools._available_cloud_models`, or it depends on live credentials.

**Live end-to-end, not just unit tests**: a task created through `schedule_task` with `model=gpt-5.6-luna` ran on the cloud engine and returned correctly, while one without stayed on `qwen3.5:4b`. Voice-scheduling worked through the full chain (wake word → Whisper → tool call → timezone conversion), reporting "8:00 AM Malay Peninsula Standard Time". **Both 8am tasks then fired unattended at exactly 08:00 local on 2026-08-27** — the one-off correctly flipping to `status=completed` — which is the real proof of the whole arc.

### Open

**`[proactive] enabled = false`.** The mislabelling is fixed and `model = "gpt-5.6-luna"` is configured, but `TIER_TRIVIAL` still bypasses approval and executes immediately, and the tier is model-assigned. Measured on the same inbox: local `qwen3.5:4b` proposed 8 deletions including two invented message ids and a payment-failure notice; `gpt-5.6-luna` proposed 4 archives, no invented ids, and left the payment notice alone. That is the argument for turning it on, but **watch the first real run before trusting it unattended** — it can archive and delete real mail.

The daily 08:00 calendar-check task will keep reporting nothing: `gcalendar` is connected and indexed (the 403 was the Calendar API being disabled in Google Cloud project `708083691179`, since enabled) but the calendar is genuinely **empty**. Either cancel it or put a real event on it.

A **server-side default model** does not exist. The chat model picker is browser `localStorage` (`store.ts`'s `defaultModel`) sent per-request, so background jobs cannot read it; `[intelligence] default_model` is the only server-side lever and needs a text editor plus a restart. **Do not wire the chat picker to unattended jobs** — experimenting with a small model in chat would silently change what a 5am mail-triage run uses. Better shape: a Settings-level default that unattended features inherit, with per-feature overrides like `[proactive] model` winning. Unresolved design question: `config.toml` is hand-edited and comment-rich, so writing to it needs a round-trip TOML library or a separate overrides file.

**Never let a model picker touch**: embeddings (`nomic-embed-text`) — `knowledge.db` holds 64,703 Gmail chunks as 768-dim nomic vectors, and swapping the embedder makes every stored vector incomparable, so search would silently return nonsense rather than error; wake word (`Hey_Sage.onnx`, openWakeWord, not an LLM); STT (`distil-large-v3.5`) and TTS (Cartesia), which have their own `[speech]` config.

## Telegram: Sage on a phone (2026-08-27 to 2026-08-28)

**Setup, all outside the repo.** Bot `@Yanson_Sage_bot`, private chat id `8509134310`. Token lives in `OpenJarvis-Data/credentials.toml` under `[telegram] TELEGRAM_BOT_TOKEN` — injected into the environment at startup by `inject_credentials()`, never in the repo. `config.toml` gained `[channel]` (`enabled`, `default_channel = "telegram"`, `default_agent = "orchestrator"`), `[channel.telegram]` (`allowed_chat_ids`, `model`), `[notifications]`, and `[proactive] notification_channel`.

**Setup gotchas worth knowing.** `getUpdates` returns nothing until the user has actually *sent* the bot a message — opening the chat is not enough. Check `getWebhookInfo` first: a configured webhook silently blocks `getUpdates` entirely, and `pending_update_count: 0` means Telegram itself has nothing queued, i.e. the message never reached that bot. `allowed_chat_ids` is the only thing preventing anyone who finds the bot from talking to Sage — keep it an allowlist.

**Inbound chat already worked; three things around it did not.**

`serve.py` has always called `JarvisSystem.wire_channel()`, which registers an `on_message` handler in `system/core.py`. **A caution from this session: I claimed nothing registered `on_message` — wrong**, because I grepped only `server/` and `cli/serve.py`, not `system/core.py`, and nearly added a second handler that would have sent duplicate replies to every message. **I also read "no new row in `traces.db`" as proof a message never arrived.** It had arrived; channel chats were simply never traced. **The real evidence for channel activity is `sessions.db`'s `session_messages`, not `traces.db`.**

- `2c7d948` — channel conversations are now traced. `QueryOrchestrator` only wraps the agent in a `TraceCollector` when the system carries a `trace_store`, and the channel's system was built without one. Note **only the agent path is traced**: `ask()` with no agent goes straight to `engine.generate()` with no collector at all. Channels set `channel.default_agent`, so they are covered; that wider gap is real and untouched.
- `cd02f29` — `[channel.telegram] model` answers phone messages on `gpt-5.6-luna`. `wire_channel(bridge, model=...)` passes it per-call through the `ask(model=)` override rather than rebuilding the system, which made the fallback nearly free: a failed call **retries once on the server default**, because no signal or a provider outage should not leave the sender with nothing. The retry is skipped when no override is configured, since it would repeat the identical failing call. Configured per channel rather than globally so a future channel can stay local. Measured **~$0.004/message**; the 16–19k prompt tokens are system prompt plus 22 tool definitions, not the message, so a one-word question costs nearly the same as a long one.
- `4368a99` — **the significant one. `ask()` never applied the configured persona.** Asked "who are you" over Telegram, Sage replied *"I'm OpenJarvis, a helpful AI assistant running locally on your hardware"* — wrong identity, and untrue on a cloud model. `serve.py` builds a `SystemPromptBuilder` for the **web chat agent only**; `prompt_builder` appeared nowhere in `orchestrator.py` or `core.py`. **So every `ask()` path — Telegram, every scheduled task, the proactive summary — ran with no persona, no SOUL/MEMORY/USER.md, and none of `config.toml`'s system-prompt rules, including the "never claim a side-effecting action you did not perform" rule added after the Spotify hallucination.** Now built lazily on the orchestrator and cached, since the builder freezes a prefix per instance for prompt-cache stability. An explicit `system_prompt` still wins (operators pass one) and agents whose constructor lacks the parameter are untouched. **If a non-web path behaves as though it does not know who it is, check whether it goes through `ask()`.**

**Notifications now fan out (this session).** `notify_windows.deliver()` sends the desktop toast *and* any `[notifications] channel`, and `notify_class_schedule` routes through the same function, so both reach the phone. Either destination arriving counts as delivered — it raises only if every configured destination fails, so a muted desktop cannot make a delivered phone notification look like an error. A new config section also has to be added to the `top_sections` tuple in `config.py`'s loader or it is silently ignored; `[notifications]` read as empty until that was done.

**Also fixed:** `config.py`'s long-standing `wake_word_model` E501 (comment moved above the field). That file now lints clean, so a future `ruff check` on it no longer needs triaging against a stashed tree.

**State:** `[proactive] enabled = true`, `model = "gpt-5.6-luna"`, first run 2026-08-28 05:00 local — **had not yet fired when this was written (01:51)**. Enabled on the strength of a read-only dry run on the real inbox: the cloud model proposed 7 **archives** (reversible), assigned **no `trivial` tiers** so nothing bypasses approval, and the one hallucinated `doc_id` it produced was still dropped by `d1c49c7`'s validation. The same inbox on `qwen3.5:4b` had proposed 8 *deletions*, two invented ids, and wanted to delete a payment-failure notice. Residual risk unchanged: `TIER_TRIVIAL` bypasses approval and the tier is model-assigned — the cloud model simply did not use it in two samples. Note the 5am run will now also carry the persona and its anti-hallucination rule, which it did **not** have when that dry run was measured.

**Still not wired: reply-to-approve.** `parse_approval_response()` in `tools/proactive_tools.py` is documented as "call from any channel message handler" but **nothing calls it** (grepped). A Telegram reply of `{id} yes` goes to the chat agent, which has no idea what it means, so approving still requires the web UI Bell. It is a handler hook, not new infrastructure — the obvious next piece, and the one that would make the 5am run actionable from bed.

**Verified live:** messages from Telegram reach the orchestrator with tools (a "Play a song" request ran Spotify control), replies arrive on the phone, conversations persist per-chat in `sessions.db`, and traces now record with `model=gpt-5.6-luna, engine=multi`. `notify_windows` returned `delivered: ['desktop', 'channel']`. The sleep-reminder task fired at 22:00 on `gpt-5.6-luna` and really called `notify_windows` (`success: true`) — the user did not see the toast, and settings checked out (`ToastEnabled=1`, no Focus Assist), so the likely cause was `duration: "short"` (~5s) during a restart; unresolved, and the reason the channel fan-out matters.

## Deepgram Flux STT, and three bugs found under it (2026-08-28)

### Flux: three voice modes, off by default

Local faster-whisper is unchanged and remains the default and the fallback. Two new modes sit beside it: **Flux Standard** (streaming, model-decided `EndOfTurn`) and **Flux Ultra** (adds speculative `EagerEndOfTurn`).

**Protocol, verified against Deepgram's live docs — do not re-derive it.** `wss://api.deepgram.com/v2/listen`, header `Authorization: Token <key>`, `model=flux-general-en`, `encoding=linear16`, `sample_rate=16000`. `eot_threshold` 0.5–0.9 (default 0.7); `eager_eot_threshold` 0.3–0.9 and **must be ≤ `eot_threshold` or Deepgram rejects the connection**; `eot_timeout_ms` 500–60000. Events arrive as `TurnInfo` with `event ∈ StartOfTurn|Update|EagerEndOfTurn|TurnResumed|EndOfTurn`. **Confidences are strings** (`"0.85"`), not numbers.

- `speech/flux.py` — client. Thresholds are validated **locally** so a misconfiguration is a clear error rather than an opaque mid-turn socket close, and `build_url()` **omits `eager_eot_threshold` entirely** in Standard mode, since its presence is what enables speculation.
- `speech/speculative.py` — the safety core. **The tool boundary is structural**: `generate_speculative()` calls `engine.generate()` with no executor, no agent and no `tools=` argument, so there is nothing to invoke. `SpeculativeManager` gates release on turn index, not-cancelled, and a normalised transcript match, allows exactly one release per turn, and `looks_tool_capable()` discards anything action-shaped — biased toward discarding, since a wasted speculation costs latency while a wrongly-released one costs an action.
- `server/flux_routes.py` — authenticated proxy at `/v1/speech/flux`. The key never reaches the browser. **Speculation runs server-side**, so speculative text never crosses the wire until released: `EagerEndOfTurn` starts a task, `TurnResumed` cancels it, and `speculative_answer` is attached **only** inside the `event.is_final` branch after `release()` verifies identity.
- Frontend: `hooks/useFluxSpeech.ts` (per-session socket, audio sent only between `beginTurn()`/`endTurn()` so idle mic audio is never transmitted, a `"stop"` frame between turns instead of a reconnect, a rolling 30s fallback buffer, and the `useWakeWord` session-token guard); Settings toggles with Ultra dependent on Flux; `InputArea.tsx` branching the hands-free entry points.

**Event interpretation is a pure `interpretFluxMessage()`** returning a discriminated action, because the frontend has **no jsdom and no testing-library** — only vitest with pure-logic tests, so a hook cannot be rendered. That made 25 real state-machine tests possible (duplicate/out-of-order finals, `TurnResumed`, malformed JSON, unknown events, released-answer handling).

**Measured live**, same clip, from last speech sample: Flux Standard `EndOfTurn` **+797 ms**; Flux Ultra `EagerEndOfTurn` **+328 ms**, `EndOfTurn` **+735 ms**; local **850 ms silence + ~660–700 ms warm transcription** (~6 s cold). A real speculative generation on `gpt-5.6-luna` took **~3000 ms**, so the ~400 ms eager lead absorbs part of it — **the Ultra win is real but partial, not "instant"**, and it still pays for a discarded call on `TurnResumed`. Re-measure before recommending Ultra on a faster model.

### `serve.py` misses whatever `SystemBuilder` wires — third instance

`jarvis serve` deliberately avoids `SystemBuilder.build()` (~30–40 s double build, #263) and hand-assembles inline, so **every dependency the builder injects is silently absent**. Previously this hid a systemless `TaskScheduler` and an untraced channel system. This time: **`RetrievalTool` was built as a bare `tool_cls()` with no backend**, so every retrieval in the web UI answered "the memory backend isn't currently configured" and **the user's 64k-chunk Gmail corpus was unreachable from chat**. Fixed with a shared `_build_tool()` helper applied at **both** construction sites (web agent and channel agent). **When adding anything to `SystemBuilder`, check `serve.py` separately.**

Even with the backend fixed, *"check my inbox"* still produced "no email tool is available" with **no tool call at all**, while *"search my knowledge base"* worked. The description said only "Search the knowledge base", with nothing linking *inbox/email/Gmail* to it — **a tool the model cannot name, it will not call.** Rewritten to name the actual contents and to state there is no separate email tool. Same class of fix as the `open_app`/`spotify_control` rewrite.

### Google OAuth expired after 7 days, and the failure was reported as good news

The first real proactive run (05:00) reported **"Nothing to report"** having fetched **nothing at all** — `gmail`, `gcalendar` and `google_tasks` all failed with `invalid_grant — Token has been expired or revoked` (confirmed directly against Google's token endpoint). A refresh token from an OAuth app in **Testing** publishing status expires after seven days; Gmail was connected on the 21st and this broke on the 28th. **Publishing the app is the durable fix, or this recurs weekly.**

`jarvis connect gmail` cannot repair it — it skips OAuth whenever a token file exists, and an expired refresh token leaves that file in place, so it goes straight to a failing sync. New `scripts/reauth-google.py` calls `run_connector_oauth` directly (same pattern as `reauth-spotify.py`), preserving stored client credentials; one consent covers all five Google connectors. **Re-authed and synced successfully: token refresh 200, gmail 64,703 → 64,982 chunks, and `gcalendar` indexed for the first time at 30 chunks.**

**The dangerous part was the reporting, not the token.** `ProactiveAgent` now reads `digest_collect`'s `sources_failed`/`total_items` metadata: a total failure returns early with "this is not a report that your inbox is clear" plus the errors, and a partial failure appends which sources were unreadable. A clean run says nothing extra. **A source that failed to fetch is not a source with nothing in it** — and "nothing to report" is the one summary a user acts on by doing nothing.

### Verification

**1,051 passed** across `tests/agents/ tests/tools/ tests/speech/ tests/server/`; **1,706 passed** on a wider run including `tests/cli/`. Frontend: **69 vitest**, `tsc --noEmit` clean. Ruff clean on every new and changed file — `config.py`'s long-standing `wake_word_model` E501 was also cleared, so that file finally lints clean. Pre-existing failures left alone and confirmed identical on a stashed clean tree: `test_base_agent::test_default_max_turns`, two `test_claude_code::TestEnsureRunner` cases, `test_manager::test_checkpoint_retention_max_5`, `test_cli::test_importing_cli_does_not_import_numpy`, `test_scan::test_run_quick_returns_subset`, `test_credentials::test_file_permissions` (POSIX modes on Windows), and four `TestGemmaCppLive` needing Kaggle weights.

**Four bugs were found by the user speaking to it live, none reachable by these tests** — all in `InputArea.tsx`, which has no renderable test harness here:
1. The Settings toggle could never be switched on: `flux_available` was `is_available() AND config.flux_enabled`, but that config is *server* state while the toggle is *client* state. **A capability flag must never be gated on the choice flag it exists to enable.** `flux_enabled`/`flux_eager_enabled` are now server-side kill switches defaulting **True**; capability is the key's presence.
2. The wake word re-fired mid-question and the mic button never lit: **in Flux mode `speechState` never leaves `'idle'`**, so the wake word stayed armed through the whole utterance. Fixed with one `effectiveSpeechState` used at every site that asks "is the mic live".
3. The mic button could not pause a Flux turn — same cause, one site missed, and pressing it fell into the *start* branch, un-suspending the wake word and beginning a competing local recording.
4. `retrieval` had no backend (above).

**Lesson worth carrying: when a new mode bypasses an existing state variable, grep every read of it.**

### Open

**Deepgram credentials note:** the key lives in `OpenJarvis-Data/credentials.toml` under `[deepgram] DEEPGRAM_API_KEY`. It was first saved as `[Deepgram Flux]` / `Api_key_deepgram` — an **unquoted space makes the whole file invalid TOML**, which broke *every* credential including OpenAI and Tavily, and the key name never reached `DEEPGRAM_API_KEY`.

Not done: live smoke tests for **Ultra** specifically, mid-turn interruption, and Flux-fails-mid-turn fallback. Standard mode is confirmed working by real speech. ~~The `[proactive] model` setting is unverified.~~ **Resolved — see the section below: the model was taking effect; the run log was misreporting it.**

## Two silent-failure bugs, both "it ran fine and reported nothing" (2026-08-28, later)

**HEAD `2866d34`**, two commits on `feature/sage-customization` (`b49660e`, `2866d34`). Working tree clean except `frontend/tsconfig.tsbuildinfo` (build artifact, never staged) and a new untracked `PRIVACY.md` (see "Google OAuth" below). **6 commits ahead of `origin`, not pushed** — confirm before pushing, as always.

Both bugs share a shape worth naming: **a component reported success while silently doing the wrong thing, and the report itself was the misleading part.** Neither was reachable by the existing tests.

### `[proactive] model` was working; the run log was lying

The 05:00 digest logged `model: qwen3.5:4b, engine: multi` despite `[proactive] model = "gpt-5.6-luna"`. Checked by constructing the agent exactly the way `orchestrator._run_agent` does — **positionally**, `agent_cls(run_engine, run_model)` with the system model — which is the only path a scheduled run takes: `agent._model` came out `gpt-5.6-luna` on a `CloudEngine`. **The override was fine.**

`_run_agent` built its result dict from `run_model` and `engine_key` — the values it *passed in*, not what the agent ended up on. Any agent that swaps its own engine/model during `__init__` (which is exactly how `ProactiveAgent` honours `[proactive]`) was misreported. Fixed in `b49660e`: capture `ag._model`/`ag._engine` after construction and reverse-map the engine to its registry key via `EngineRegistry`, falling back to the passed-in values when nothing was overridden. Same class of fix as `0bb493b`'s `run_engine_key`.

**Why it mattered enough to chase:** that log line is the only evidence that the agent trusted with mailbox actions is on the model it was tiered against. A wrong model name there is indistinguishable from a real regression.

The gap that made it ambiguous is closed too: the existing tests exercised `_apply_configured_model` **in isolation** and never the positional path the orchestrator actually uses. `TestConfiguredModel::test_positional_construction_ends_up_on_the_configured_model` now goes through the real `__init__`.

### Reading the class schedule cancelled the day's reminders

The user got no notification for an 11:00-13:00 class. The 10-minute `notify_class_schedule` loop had run all morning — including 10:41 and 10:51, squarely inside the 15-minute window — and reported **"Nothing upcoming"** every time.

`CheckClassScheduleTool._drop_already_notified` wrote the "already notified" marker as a side effect of **merely checking**. At 08:00:06 the *other* scheduled task — *"Check my calendar for any events scheduled before 9:00 AM today"* — summarised the day with a wide lookahead, and that read marked all three of Friday's classes as notified, three hours before the class. Its own output even lists the 11:00 class back to the user: it saw it, described it, and consumed the reminder in the same call.

**Not a one-off.** That task runs daily, so it burned every class every day. Asking *"what's my schedule today"* did the same — `check_class_schedule`'s own description tells the model to answer that with `lookahead_minutes=1440`.

Fixed in `2866d34`. Suppression is a property of having **notified**, not of having **looked**, so it moved to `NotifyClassScheduleTool` and is recorded only after `deliver()` actually succeeds. That also fixed a second bug in the same code: **a failed toast still marked the class notified**, so a transient notification-backend failure silently cost the user the reminder for the rest of the day instead of retrying ten minutes later. Partial failures are handled — first toast lands, second throws, only the second is retried. `check_class_schedule` is now the pure function its docstring already claimed it was.

The module docstring had asserted the opposite of the truth the whole time: *"Deliberately does not send any notification itself"* and *"a pure, easily-testable function of (schedule file, now) -> upcoming classes."* **A docstring is not evidence.**

**Live state fixed by hand:** `class_schedule_notify_state.json` had all three Friday classes burned; cleared the two future ones so that day's 15:00 and 17:00 reminders still fired. (A dry-run during verification re-burned one of them through the real state file — caught and restored. **`notify_class_schedule` writes real state even with `deliver` patched**; pass a `state_path` pointing at a temp dir when exercising it against the live schedule.)

### Verification

`146 passed` (`tests/agents/ tests/system/ tests/scheduler/`) for the model fix; `740 passed, 4 skipped` (`tests/tools/`) for the schedule fix. Ruff clean on every changed file.

**Every new regression test was checked against the old code and confirmed to fail there** — three for the schedule fix, four for the model fix. Worth keeping as a habit here: two of the tests as first written would have passed on the broken code (a standalone checker used a *different* state file than the notifier, so the coupling under test was never actually exercised). **A regression test that has not been run against the bug is not yet evidence.**

Pre-existing failures confirmed identical on a stashed tree, left alone: `test_base_agent::test_default_max_turns`, `test_persona_scope::test_named_persona_resolves_to_personas_dir`, `test_channel_contract[whatsapp_baileys]`, two `test_claude_code::TestEnsureRunner` cases. Also **`tests/tools/test_check_class_schedule.py::test_check_class_schedule_registered` fails when that file is run alone and passes in a full run** — it calls `register_value` on a tool the import already registered, so it depends on another test importing the module first. Pre-existing, order-dependent, not worth chasing mid-task but worth a one-line fix someday.

**Live-verified after restart** by asking the running server *"What is my full class schedule for today?"* — the exact query that used to burn the reminders — and confirming the state file was byte-identical before and after.

### Google OAuth: still Testing, publish deferred by the user

The 7-day refresh-token expiry is **not fixed**; the user chose to defer it. Publishing is blocked behind Google's Branding requirements (app name, support email, homepage URL, privacy policy URL) — `Publish app` is greyed out until all four are set.

Groundwork done, so picking this up is short:

- `PRIVACY.md` drafted and committed at the repo root (on `feature/sage-customization`, **not on `main`**, which is where the URL below needs it). Deliberately truthful about the cloud-model path (email/calendar excerpts leave the machine when a cloud model is configured), which is what a reviewer checks for. It contains the user's email address as the contact.
- Homepage URL can be `https://github.com/GrimRift/OpenJarvis` — the fork is public and returns 200.
- Privacy policy URL would be `https://github.com/GrimRift/OpenJarvis/blob/main/PRIVACY.md`, which **does not resolve yet** — `PRIVACY.md` must be committed and pushed to `main` first. That is a public-facing change to the user's repo; ask before pushing.
- Publishing does **not** require Google verification and does stop the 7-day expiry. Because `gmail.modify` is a *restricted* scope, an unverified production app keeps an "unverified app" warning at consent (Advanced -> Go to Sage) and a 100-user cap — both fine for one user. Full verification needs a third-party CASA assessment: weeks and real money. **Publish unverified; do not pursue verification.**
- Until then the token expires roughly weekly. `scripts/reauth-google.py` repairs it in a minute.


## Sage persona, Web parity, and latency pass (2026-08-28)

**Feature HEAD before this handoff update: `3b43494`** (`perf: reduce Sage response latency`). The immediately preceding verified commits are `2d0e97e` (compact voice player), `f9bedce` (seven-day inbox recency), `e7bad07` (global Sage persona), `9a2ef7f` (safe clickable Web previews), and `195f563` (Telegram/Web approval execution). Nothing is pushed; confirm before pushing.

### What changed and what the user verified live

- Streaming voice replies no longer hold back completed text while Cartesia TTS runs. The server releases text first; the Web UI then synthesizes and attaches audio asynchronously. Interactive Web morning digests use the same deferred-audio path. Scheduled/channel digests retain eager audio generation.
- `digest_collect` now reads independent sources concurrently and merges them in the original deterministic source/section order. Gmail's digest-only path bounds itself to 15 messages and overlaps eight detail GETs; normal bulk Gmail synchronization remains sequential and unbounded by default.
- Live profiling before the fix: a voice identity question displayed at 17.2s although Luna generation itself took 3.5s; the rest was synchronous TTS. Morning digest was 27.7s: connector collection 10.3s, Luna 8.8s, TTS 8.6s. Gmail alone took 6.9s for 15 serial detail requests.
- Live profiling after the fix: voice text 4.35s; Gmail digest read 1.65s; connector collection 1.4s; successful morning-digest text 12.2s. The user then independently confirmed that text appears before speech and reported: identity 3.5s, Japan time 3.7s, Web search 11.0s, inbox 13.0s, morning digest 13.9s.
- Sage was rebuilt/restarted; `/health` is `ok`, `/v1/channels/status` is `connected`, ports 8000 and 5173 are listening. `frontend/tsconfig.tsbuildinfo` is removed and ignored.

Verification: **104 backend tests passed** across the changed server, speech, digest, and Gmail paths; frontend audio policy **2 tests passed**; production frontend build completed; Ruff passed on all touched files with the repo's pre-existing `routes.py:102` E501 excluded. Every new concurrency/non-blocking regression was run against the old implementation first and failed there.

### Concrete next performance and rendering work

1. **Tool/context payload is now the main latency floor.** The user's live turns still carried 18,092 input tokens for inbox, 17,535 for Web search, and 16,750 for `world_time`; even the tool-free identity response carried 8,660. Do not simply remove capabilities. Profile prompt sections versus tool schemas, then design a deterministic/lazy tool subset or stable prompt-cache-friendly routing that preserves all fail-closed boundaries and never hides a tool needed for an action.
2. **Fix two visible rendering defects from the live transcript.** Currency text spanning `$200` and a later `$1` was interpreted as inline math and rendered as concatenated `200invoiceAIcreditswereactivated...1`; protect currency from `remark-math` without breaking real math. Web research also surfaced literal `&#xA0;` and duplicated source/snippet cards; normalize escaped entities and deduplicate the raw preview without weakening citations or safe-link handling.
3. **One Luna digest attempt returned empty content** after consuming exactly 1,024 completion tokens with `finish_reason=length`; the next attempt succeeded. Add a bounded fail-closed retry/headroom policy for an empty reasoning-model result, with a regression test, rather than treating an empty generation as a valid digest.
4. The global Jarvis-like Sage persona is deployed everywhere, but the user has not yet made a final qualitative judgment on its personality. Do not rewrite it again without asking what specifically feels wrong.

## Context payload, two rendering defects, and the empty digest (2026-08-28)

Picks up items 1-3 of the previous section's "concrete next work".

### Tool schemas were the context, not the prompt

Profiled before changing anything (`tiktoken`, live 23-tool config):

| component | tokens |
|---|---|
| tool schemas (23 tools) | **3,791** |
| entire system prompt | 1,540 |
| — of which `agent_template` | 572 |
| — `soul` (SOUL.md) | 433 |
| — `user` | 257 |
| — `memory` | 108 |
| — `current_datetime` + reminder + tool_use_reminder | 170 |

**Schemas are 71% of the per-turn baseline and the prompt is not the problem.**
Worse, they are re-sent on every turn of a tool-calling loop, which is why a
single inbox question reached 18,092 input tokens. By group: coding/git 1,270,
core 744, scheduling 693, media 552, class 400, notify 132.

`agents/tool_routing.py` sends only the schemas a request could plausibly need,
filtered once per request in `OrchestratorAgent.run()` (never per turn, so the
payload stays byte-identical across the loop and a provider's prompt cache
keeps hitting).

**Measured live against the running server**, two requests differing only in
group-triggering words: **5,452 vs 7,379 prompt tokens, a 1,927-token gap.** A
real conversational turn fell from 7,365 to 5,452, so **~1,900 tokens saved per
turn**; the `world_time` question fell from 13,848 to 10,022 across its
multi-turn loop.

**The first live measurement showed those two requests 14 tokens apart —
routing was live and doing nothing.** `routes.py` injects retrieved memory into
the request as a *system* message before dispatch, `_handle_agent` feeds that
into `ctx.conversation`, and `routing_text` was reading every prior message.
A blob from a 64k-chunk corpus matches essentially every group, so the full
toolset was selected every time. Only user turns route now (`fa490e1`).

**Worth internalising: all 38 unit tests passed throughout, including a wiring
test that runs a real turn through `OrchestratorAgent.run()`.** They fed
`routing_text` a bare string, which is not the shape the server produces — the
right function tested against the wrong input. Only an A/B of two live requests
differing in one variable exposed it. An offline measurement script had the
same flaw and reported a confident, wrong 2,214-token saving.

Three properties keep it from hiding a capability, which is the failure mode
that matters — a hidden tool reads to the user as "Sage can't do that":

1. **Core tools are always sent** (`retrieval`, `web_search`, `world_time`,
   `calculator`, `notify_windows`). `retrieval` is core on purpose: "check my
   inbox", "what did I say about X" and "when is my flight" all resolve
   through it and share no vocabulary.
2. **A tool in no group is always sent.** Adding a tool cannot silently hide
   it; it must be assigned to a group deliberately. There is a test for this.
3. **Matching uses the recent conversation, not just the newest message**, so
   "open spotify" → "do it" keeps the media group.

Patterns are deliberately over-inclusive: a false positive costs tokens, a
false negative costs a capability.

**Kill switch: `[agent] route_tools = false`** sends everything. Use it to rule
routing out first if a "Sage says it can't do X" report ever appears.

**Not done — the remaining floor.** Routing trims schemas; it does not touch
the re-send-per-turn multiplier or conversation history. If more is needed,
measure those two before adding cleverness here.

### Currency rendered as math; entities and duplicates in web results

`$200 ... $1` in one answer was paired by `remark-math` and KaTeX then ate
every space between them, producing `200invoiceAIcreditswereactivated...1`.
`frontend/src/lib/currency-math.ts` escapes a `$` only when it begins
something shaped like money, leaving `$x^2$`, `$$...$$` and `$2x + 1$` as real
math, and skipping code spans and fenced blocks where a backslash would be
visible. The one real loss is inline math opening with a bare number followed
by a non-word character (`$1 + 1$`); use `$$1 + 1$$`. Escaped text is used for
rendering only — **the copy button still yields `$200`, not `\$200`**.

`web_search.py` had two defects feeding both the transcript and the model:
`_fetch_url_text` stripped tags but never decoded entities, so `&#xA0;`
survived verbatim; and Tavily can return the same page twice (canonical plus
an AMP or tracking variant), which showed as duplicate source cards and paid
for the same snippet twice in the prompt. Entities are now decoded **after**
tag-stripping (so an encoded `&lt;script&gt;` cannot become a real tag), and
results are deduplicated by URL ignoring trailing slash and fragment. Results
with no URL are never collapsed together.

Also cleared the pre-existing `image_url` E501 so `web_search.py` lints clean.

### An empty generation is not an empty news day

One live Luna digest consumed exactly 1,024 completion tokens, finished with
`finish_reason="length"`, returned no content, and the next attempt succeeded.
`MorningDigestAgent` treated the empty string as a valid briefing.

`_generate_narrative()` now retries **once** with real headroom
(`max(max_tokens * 4, 4096)`) and, if the model returns nothing twice, fails
closed: no artifact stored, no TTS, and a message that cannot be mistaken for
"you have nothing waiting". Content that is entirely `<think>` tags counts as
empty for the same reason.

Two adjacent bugs fixed in the same pass:

- **`BaseAgent._generate` could not accept a `max_tokens` override** — it
  passed the stored default positionally alongside `**extra_kwargs`, so any
  caller supplying one got "got multiple values for keyword argument". Stored
  defaults are now a dict that extra kwargs update.
- **An empty *revision* silently destroyed a good briefing.** The evaluator's
  regenerate step assigned its result unconditionally, so a low score plus an
  empty rewrite cost the whole digest. It now keeps the original.

**Finding, not fixed: `openjarvis.agents.digest_evaluator` does not exist.**
The import inside `run()` raises `ImportError` on every real run and is
swallowed by a bare `except Exception: pass`, so the self-evaluate/regenerate
step has never executed and every stored digest carries `quality_score = 0.0`.
Left alone deliberately (writing an evaluator was not in scope), but the guard
above is now correct for when it lands, and a test documents the live
behaviour. Decide whether to build it or delete the dead branch.

### "No classes today" when there were three — found by the smoke test, then fixed

Asked *"What is my class schedule today?"* at 17:05 on a Friday with three
classes on the note (11:00, 15:00, 17:00 — the last one in session), Sage
answered **"You have no classes scheduled for today."**

Not caused by routing: routing demonstrably includes `check_class_schedule`
for that wording, and a control request padded so every group loads — an
almost-unrouted toolset — reached the same wrong conclusion, then contradicted
itself by listing the three classes it had just denied.

The mechanism is in the tool. `_find_upcoming` keeps only
`0 <= minutes_until <= lookahead`, so a class that has already *started* is
never returned, and `datetime.combine(now.date(), start_time)` means it only
ever considers today regardless of how large the lookahead is. Asked directly
with `lookahead_minutes=1440` it answered "no classes starting in the next
1,440 minutes" — technically true, and useless.

So `check_class_schedule` is a *starting-soon* tool that cannot list today's
schedule once the last class has begun, while its own description tells the
model to use it for "today's full schedule" with a large lookahead. The
description promises what the implementation cannot deliver, and the model
converts "nothing upcoming" into "no classes today".

Same family as the reminder bug fixed earlier the same day: a narrow window
read as a statement about the whole day.

**Fixed with a real day view, not a wider lookahead** (which cannot help).
`full_day=true` returns every class scheduled today, each marked `upcoming`,
`in_progress` or `ended`. The default narrow mode is untouched, because the
10-minute reminder loop depends on it. The description now names both modes
and states plainly that an empty narrow result must never be reported as "no
classes today", and that raising `lookahead_minutes` will not reveal a class
that already started.

`notify_class_schedule` now forwards **only** the reminder window to the
checker instead of its caller's whole param dict: a hallucinated
`full_day=true` would otherwise have turned "a class starts in 15 minutes"
into a toast for every remaining class of the day, each burning its
once-per-day suppression.

Verified against the real note at 17:10 on the Friday in question: all three
classes listed, the 5PM one marked in progress, while the narrow window stayed
correctly silent and still fired at 16:50 for the 5PM class.

### Verification

Ruff clean on every changed file. Frontend `tsc --noEmit` clean; **84 vitest**
including 9 new currency cases.

**Every new regression test was run against the old implementation first**: 7
failed there (2 web-search hygiene, 5 empty-generation), and the 4 control
tests passed on both, as they should. Routing is new code, so its wiring
tests are the control pair instead — trimmed-payload and routing-off assert
opposite payloads through the same path.

## The rest of the latency floor: turns and history (2026-08-28, latest)

Profiled first, again, by driving the real `OrchestratorAgent` loop with a
fake engine that records every payload.

### Turns are almost pure re-send

| tool calls | engine calls | billed tokens | vs 1 turn |
|---|---|---|---|
| 0 | 1 | 2,429 | 1.00x |
| 1 | 2 | 4,861 | 2.00x |
| 2 | 3 | 7,296 | 3.00x |
| 4 | 5 | 12,211 | 5.03x |

Exactly linear, and the message content across those three turns grows
**1547 → 1550 → 1553 tokens**. The genuinely new information per turn is about
**3 tokens**; everything else is the same prefix sent again.

**That makes it a prompt-cache problem, not a payload problem**, and nothing
in the repo could tell us whether the cache was working: `CloudEngine` never
surfaced `prompt_tokens_details.cached_tokens`, so the live API reported only
prompt/completion/total. It is passed through now. **Check it before
attempting anything else here** — if the provider is serving that repeated
prefix from cache, the 5x is already mostly paid for and the remaining work is
elsewhere. Routing was deliberately built to make this cacheable: tools are
filtered once per request, never per turn, so the prefix is byte-identical
across the loop.

### History was unbounded, and multiplied by turns

`LoopGuard.compress_context` triggered on **message count only**
(`max_context_messages = 100`). Twenty prior exchanges is 41 messages — far
under it — so nothing compressed while the bill went 7,296 → 31,416 (4.31x),
because the whole history is re-sent on every turn of the loop.

Compression now also triggers on approximate size
(`max_context_tokens = 8000`, 0 restores count-only behaviour), and stage 2
walks backwards from the newest message keeping what fits, so the turn the
user is actually in always survives even if one message exceeds the budget.

**Cost now plateaus instead of growing:**

| prior pairs | 20 | 40 | 80 | 160 |
|---|---|---|---|---|
| billed | 26,592 | 26,289 | 26,289 | 25,386 |

Token counting there is deliberately `len(content) // 4`, not tiktoken: it
decides how much to drop, not what to bill, and a hard tokenizer dependency in
the loop guard is not worth the accuracy.

### What is left, in order

1. ~~Read `cached_tokens` from a live turn.~~ **Answered — see below.**
2. **Consider lowering `max_context_tokens`.** 8,000 is a safe default that
   only bounds the worst case; a tighter budget trades recall for latency, and
   that is the user's call, not a default worth guessing at.
3. **Summarising history rather than dropping it** was an explicit earlier
   request ("rather than the entire chat history we should create a summary").
   The window is the cheap version; a rolling summary is the real one.

### Verification

19 loop-guard tests including the plateau behaviour, the newest-exchange
guarantee, and a single oversized message still leaving a non-empty
conversation. `1,504 passed` across `tests/agents tests/engine tests/server`;
the 8 failures are all on the known list above (`test_default_max_turns`, two
`test_claude_code`, `test_manager` checkpoint retention, four
`TestGemmaCppLive` needing Kaggle weights).

### Answered: the provider does cache, so turns are mostly free

Measured directly against the cloud engine, same 2,416-token prompt twice:

```
call 1: prompt_tokens 2416, cached_tokens 0      <- cold
call 2: prompt_tokens 2416, cached_tokens 2413   <- 99.9% served from cache
```

Turn 2 of a tool loop is turn 1's messages plus a tool call and its result, so
turn 1's payload is a literal prefix of turn 2's and is served from cache. **The
5x turn multiplier is nominal — it shows in `prompt_tokens` but not in real
cost or latency.** Do not spend effort compressing turns. This is also why
`tool_routing` filters once per request rather than per turn.

### The trimming shipped an hour earlier would have broken exactly that

`compress_context` ran *inside* the turn loop on a growing message list. Its
sliding window keeps the newest messages, so as tool results accumulate the
start of the kept context moves every turn — a different prefix each time,
missing the cache on precisely the long conversations where trimming triggers.
It would have made long chats slower, not faster.

Fixed: the token budget is applied **once per request** via
`OrchestratorAgent._trim_history_once()`, before either loop starts. Inside the
loop `compress_context(..., apply_token_budget=False)` leaves only the original
count-based overflow recovery, which is a genuine safety valve and rare enough
that a cache miss there is the right trade. Both the function-calling and
structured loops are covered.

Two tests hold the line: one asserts the context start does not move across
five loop turns, and a contrast test asserts that per-turn trimming really
does move it — so this stays a demonstrated problem rather than a remembered
one.

**Live A/B of routing, in seconds rather than tokens** (same question, only the
tool list differing, median of 3): all 23 tools **1.17s** vs routed 5 tools
**0.95s** — about **0.22s per model call**, so roughly 0.4–0.7s on a request
that uses a tool. Real but modest; the large win was the earlier deferred-audio
work, 17.2s to 4.35s.

## Scope: rolling conversation summary (not built)

The window bounds cost by forgetting. A summary bounds it by compressing. Build
this only once the window is observed actually losing something the user
wanted — it may never be worth it.

**Shape.** Split the conversation into an immutable summarized head and a
verbatim tail:

```
[system] [tool schemas] [summary of exchanges 1..N] [exchanges N+1..now]
         └───────── stable, cacheable ──────────┘   └── grows ──┘
```

- Summarize only the portion older than the last K exchanges (K ~ 6).
- **Summarize once and persist**, keyed by conversation id and the index of the
  last summarized message. Never per request, never per turn.
- Fold incrementally as the tail grows past K again.

**Where.** Not in `LoopGuard` — that is per-turn overflow recovery and must stay
that way. This belongs where the conversation is assembled, before the agent
loop: `routes.py`'s context injection or `_handle_agent`, next to the existing
memory-context injection. Storage is one more column in `sessions.db`.

**Which model.** Local `qwen3.5:4b`, off the critical path (after a turn
completes, not before the next starts). Paying cloud latency to summarize text
the user has already read defeats the purpose.

**Invariants.**
- The summary must be byte-identical across turns and across consecutive
  requests. Regenerating it per request is *worse* than the window — see the
  cache finding above.
- Fail closed to the sliding window if summarization fails or has not run.
- The newest exchange always survives verbatim.
- Never summarize tool results. An agent acting on "the tool returned
  something" is the failure mode this project keeps hitting.

**Risks.** Silent fidelity loss is the real one: a summary that drops the detail
that mattered reads as Sage misremembering and is invisible to tests. Keep K
generous. Second, drift across repeated folds — cap the fold count and keep raw
messages in `sessions.db` so any summary can be rebuilt from source.

**Testing.** A pure `plan_history(messages, summary, budget) -> (summary_block,
tail)`, testable without a model, in the shape of `tool_routing` and
`orb-motion`. Then: stability across turns, fail-closed on summarizer error,
newest-exchange survival, and a fold that does not lose a named fact.

**Effort.** Roughly one session; the planning function and its tests are the
bulk.

## Flux teardown, the proactive all-clear, and a TTS voice audit (2026-08-29 to 2026-08-30)

Three commits, all pushed to `feature/sage-customization`: `978c2d13`,
`c36d8fae`, `624f0e36`.

### A wake word that hears nothing now releases the microphone

Saying "Hey Sage" and then staying silent left the orb in LISTENING
indefinitely — an hour, in the user's report. Deepgram's `eot_timeout_ms`
bounds finalising a turn *in progress*; it does not bound waiting for one to
begin, and Flux only ends turns it started. So nothing on either side was
responsible for a turn that never happened.

`FLUX_SILENCE_TIMEOUT_MS = 8000` in `InputArea.tsx`, armed at both Flux entry
points (`beginAutoRecording`, `beginWakeWordRecording`), cancelled by a new
`onTurnStarted` callback surfaced from `useFluxSpeech.ts`, and cleared at every
existing turn-end site plus on unmount.

### Ending a Flux turn was closing the whole Flux connection

The fix above exposed a bug that had been there the entire time. The browser
sends `'stop'` between turns rather than streaming idle microphone audio, and
its own comment says this stops the proxy forwarding *"rather than closing the
socket"*. The relay disagreed: `pump_audio` **returned** on `'stop'`, which
completed that task, cancelled the event pump via `asyncio.wait(FIRST_COMPLETED)`
and closed the Deepgram session. Every turn tore the connection down and the
next one paid for a fresh handshake.

It stayed invisible while a turn always ended immediately before a reply. The
8s timeout calls `endTurn()` with no turn in progress, so the close arrived out
of nowhere and surfaced as the toast **"Cloud transcription unavailable — using
local. Flux connection closed"**, eight seconds after a wake word that heard
nothing.

The relay now keeps reading after `'stop'`. There is nothing to forward until
the next turn sends audio, so waiting for it is the whole behaviour.

Second half: a dropped socket only ever called `fail()`, and nothing reconnects
outside the `enabled` effect — so **any** transport blip stranded the page on
local transcription until reload. Added a bounded reconnect: three attempts at
500/1000/2000ms (`reconnectDelay`, a pure exported function so it is testable
without jsdom), budget reset once a `FluxReady` proves the socket good, and
never retried for a `FluxUnavailable` verdict, which is the server deciding Flux
must not be used.

### An empty proactive classification was being read as an all-clear

Audit of the 2026-08-29 morning runs (below) found the 05:00 proactive run had
logged `llm raw (0 chars)`, parsed 0 proposals from a digest that *had*
collected items, and reported "Nothing requires your attention."

`proactive_agent.py` already refuses to draw that conclusion when its sources
fail to fetch — *"Every source failed, so this is not an all-clear."* A model
that says nothing is the same silence wearing a different hat, and that guard
never covered it. Note this is a **different agent** from `morning_digest.py`,
which got its own bounded retry earlier; the two are easy to confuse.

`_classify()` retries once. `finish_reason` distinguishes the two kinds of
empty: `"length"` ran out of budget so the retry buys headroom (the ceiling here
is already 8192, set in `__init__` for JSON-array room — so this is *not* the
digest's 4x bump); anything else came back blank, where the digest path observed
the very next attempt succeeding unchanged.

Still empty after the retry is **recorded, not announced** — the user's explicit
choice: `metadata["classification_empty"]` plus a warning naming the collected
item count, no 05:00 notification. Rare enough to matter: 1 of 160 logged calls.

### Morning scheduled tasks: audited, all firing

Read from `scheduler.db`. **Timestamps are stored UTC and the user is UTC+8, so
the morning runs sit under the previous UTC date** — querying `started_at >=
'<today>'` silently hides everything before 08:00 local.

| Local | Task | Result |
|---|---|---|
| 05:00 | Proactive agent | OK (but see the empty classification above) |
| 08:00 | Calendar / reminders | OK |
| every 10 min | `notify_class_schedule`, 15-min lookahead | 57/57 OK |

Two source problems, **neither fixed, both open**:
- `google_tasks` returns `403 Forbidden` on `users/@me/lists` every run. Scope
  or consent; adjacent to the still-pending Google OAuth publish.
- `imessage` and `slack` report "not connected (no credentials)" every run.
  iMessage cannot work on Windows, so it is permanent noise in the digest's
  "I couldn't read ..." line. Decide: connect Slack, or drop both from the
  source list. Ask the user first.

### Temperature audit

Verified against the live config, not the dataclass defaults:

| Path | Value | |
|---|---|---|
| `intelligence.temperature` | 0.7 | not set in `config.toml`; code default |
| Speculative / Flux Ultra | 0.7 | via `_configured_temperature(config)` — the earlier repeated-joke fix is holding |
| `ProactiveAgent` | 0.2 | deliberate, `__init__`, for reliable JSON |
| `DeepResearchAgent` | 0.3 | deliberate |

No drift. `test_it_matches_the_configured_temperature` pins the speculative one.

### Cartesia Sonic 3.6 voices (implemented 2026-08-30)

All Sage TTS paths now use the centralized profiles in
`speech/voice_profiles.py` and Cartesia `sonic-3.6` with the current
`generation_config` request shape.

- **Jarvis** is primary: `78a05d7d-268b-4a18-aad7-7a96902a95ee`, speed `1.0`,
  volume `1.9`.
- **Frieren** is alternate: `e23c9ecf-e002-4f7a-8e39-13d18d09923f`, speed
  `0.9`, volume `1.9`.
- Settings -> Speech -> Sage voice exposes both choices and persists the
  selection locally. It controls streamed replies, batch fallback, deferred
  morning-digest playback, and matching pre-rendered wake acknowledgements.
- Scheduled/direct digest audio defaults to Jarvis through `DigestConfig`.
  Digest content is generated fresh; there is no pre-recorded digest.
- The previous wake clips and cached digest audio were removed. Twenty-nine
  exact cached-digest audio pointers were cleared without removing digest text.
- Both voices' three wake clips keep the original words (`Sir.`, `Yes, sir.`,
  and `Hello, sir.`) but use Sonic's primary `content` emotion for warmer,
  greeting-like delivery. Each voice can be regenerated independently.
- The speaking orb's analyser was under-driving the designed animation range:
  measured Jarvis audio at volume 1.9 reached only 18-39%. RMS gain is now 8.0
  with faster 0.65 attack / 0.22 release, so size and brightness articulate
  real syllables across most of the 0.95-1.40 speaking range.

Live verification after rebuilding `frontend`: both choices
persisted in the Web UI; six versioned wake clips returned HTTP 200; paid batch
MP3 and streaming PCM synthesis completed for both profiles. Frontend: 158
passed before the orb retune, then 160 passed after it. Focused
Python/config/server suite: 118 passed. Ruff and `git diff --check` clean. The
currently elevated `jarvis serve` process could not be restarted from Codex,
but static assets are served from the rebuilt bundle and were live-reloaded.
All source, tests, and generated wake assets for this slice are tracked on
`feature/sage-customization`.

### Verification

- `tests/server/test_flux_routes.py` — 27 passed. New
  `TestStopKeepsTheSessionAlive` drives real audio either side of a `'stop'`;
  confirmed failing against the un-fixed relay.
- Frontend — 153 passed, `tsc` clean, bundle rebuilt.
- `tests/agents/` — 648 passed. New `TestEmptyClassificationIsRetried`: 7 tests,
  **5 of which fail against the un-retried code** (the other 2 cover the
  metadata flag, which is independent of the retry — not evidence).
- Ruff clean on every touched file.

`AGENTS.md`'s pre-existing-failure list gained `test_manager::TestCheckpoints`:
one case fails per run and *which* one moves (`test_checkpoint_retention_max_5`
and `test_get_latest_checkpoint` both seen); each passes alone.

## Current milestone and next agenda

M28 is shipped. The M31-adjacent wake-word and voice-customization slice is now
functionally complete: the wake model, selectable Sonic 3.6 voices, matching
acknowledgement clips, digest voice routing, speech-only sensitive-value
sanitization, sentence-buffered incremental TTS, Stop semantics, adaptive
stream rendering, and speaking-orb response are all implemented and verified.
The orb now follows actual queued/audible playback through the final
output-latency tail, and long tool-assisted conversations remain provider-valid
after trimming. There is no known open issue in this voice slice after user
acceptance.

Next candidates, in order:

1. Observe normal conversations after the atomic tool-history repair. The
   reported summary failure was malformed trimming rather than proof that a
   rolling summary is required; build the deferred rolling summary only if the
   valid 8,000-token window later loses useful conversational detail.
2. Close remaining integration hygiene: resolve Google Tasks/OAuth publishing,
   and either connect Slack or remove the permanently unavailable iMessage and
   unused Slack checks from the digest.
3. Run the remaining live Ultra-voice interruption/fallback smoke coverage if
   Ultra mode will be used regularly.
4. Do not begin Microsoft Calendar, OneDrive, or Teams work until the existing
   Microsoft admin-consent blocker is cleared.
