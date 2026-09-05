# Claude Code continuation — 2026-09-05

## First task: verify voice, then resume M35

The user is switching to Claude Code. Stop Codex edits; one active editor.
Do not add an audio player, replay controls or sliders. Intended everyday
voice UX is ephemeral streamed speech. User explicitly selected **only fix
stream authentication for now** after being asked about fallback redesign.

1. Ask the user whether a hard-refresh followed by one voice reply now speaks
   normally and rearms. If not, measure the actual failure before changing code.
2. Preserve the existing fallback pending a separate decision. It is already
   present in InputArea.tsx: after a stream fails before audio, it synthesizes
   a batch clip and attaches AudioPlayer. This is a known mismatch with the
   intended UX, not a newly authorized redesign.
3. Once voice is confirmed, continue private phone setup: Tailscale on PC and
   iPhone, authenticated Siri Shortcut, then actual audio → Waze ordering.
   Tailscale installation/account state has NOT been inspected or confirmed.

## Git and rollback

- Repo `C:\AI\OpenJarvis-Lab`; branch `feature/sage-customization`.
- HEAD `19c2f2e86976e3866028a9f8f6ba2c27579a959a`; M35 and TTS fixes are
  **uncommitted and unstaged**, including new untracked files. No push made.
- Annotated rollback tag `rollback/pre-m35-2026-09-05` points to HEAD.
  The tree was clean before this work. CI succeeded on `fdb35337`; only
  AGENTS.md/HANDOFF.md/ROADMAP.md differ between that commit and the tag.
- Live config is external: `C:\AI\OpenJarvis-Data\config.toml`.
  Pre-M35 backup: `config.toml.pre-m35-20260905` beside it.
- Git cannot roll back user environment variables or the Python environment.
  Preserve current work before any rollback. Never `git add -A` (vendored
  CPython tree). Do not commit or push unless requested.

## Implemented changes

- `src/openjarvis/tools/navigate.py`: registered read-only tool; saved names
  in external `saved_places.json`, supplied coordinates, optional Google Places
  candidate selection, traffic-aware Routes, destination weather, Waze links.
  No location guessing from the PC, no app launch, no saved-place writes.
  Unknown ETA/traffic stays unknown. Fixed endpoints, bounded requests, no
  redirects/retries, no credential-bearing provider error text.
- `src/openjarvis/core/config.py`: NavigationConfig and TOML loading, all flags
  default false. Live flags are now true after user approval/setup.
- `src/openjarvis/tools/__init__.py`: tool discovery; external `[agent].tools`
  now includes navigate. Security audit classifies navigation egress.
- `src/openjarvis/server/drive_routes.py` + app.py mount: `/v1/drive` requires
  Sage bearer authentication and phone origin. Shared tool executes in a worker
  thread; optional Cartesia MP3 uses Jarvis/Frieren profiles and spoken-text
  sanitizer, returns inline base64, no audio file persistence. Candidate choice
  precedes synthesis; audio failure preserves the link. Cache-Control no-store.
- `frontend/src/hooks/useStreamingTts.ts` uses new
  `frontend/src/lib/speech-transport.ts`: respects configured API base and uses
  existing `buildWsProtocols()` authentication. New tests pin wiring.
- **AudioPlayer.tsx has no remaining diff.** Codex's attempted player fix was
  undone after the user's correction. InputArea.tsx fallback is untouched.

## Setup and measured evidence

- User privately set `GOOGLE_MAPS_API_KEY` and separate `OPENJARVIS_API_KEY`
  Windows user variables. Do not display/read out/enter/copy them to docs.
- Google Routes and **Places API (New)** enabled on the same Google key;
  separate 30 requests/day caps user-confirmed. Traffic requests use Routes
  **Pro**, not Essentials; place names/coordinates use Text Search Pro.
- Live authenticated `/v1/drive`: SM City Calamba resolved, route 436 seconds /
  1,920 metres, weather, Waze URL, 298,029-byte MP3; HTTP 200 in 5.2 seconds.
  Without auth: HTTP 401. Phone playback is NOT yet verified.
- Auth change initially broke the browser: unauthenticated `/v1/models` returned
  401; authenticated returned five models through both backend and Vite proxy.
  Settings' Connected indicator is only public health, so it misleadingly stayed
  green. User was directed to enter their Sage secret in Connection → API key.
- TTS failure reproduced: unauthenticated TTS socket rejected; authenticated
  accepted. Batch synthesis 200, plain media fetch 401, authenticated fetch 200
  (27,693 bytes). Existing player hides itself on error, explaining its flash.
- Authenticated live TTS stream completed with 199,680 PCM bytes. Stream-only
  fix has 42 focused frontend tests passing, including wiring/architecture;
  frontend built successfully after player changes were reverted. No observed
  real microphone → reply → rearm cycle after the final fix yet.
- Earlier backend checks: 143 tests across navigation/weather/config/architecture;
  endpoint/navigation/architecture checks later passed 66 tests. Ruff src/tests
  clean. No complete repository suite run. Wiring tests failed on baseline.
- An attempted isolated headless player probe failed in the test harness
  (`createRoot is not a function`), before mounting; it is NOT evidence of a
  Sage player defect or successful playback. Do not report it as verification.

## Runtime and remaining risks

- Use existing Start menu `Sage.lnk` / `Stop Sage.lnk`; start launcher runs
  backend 8000 and Vite 5173. Browser URL: `http://localhost:5173`.
- Start-Process inherits its parent's environment. A long-running agent shell
  may not have newly created user variables; inherit them privately when
  restarting, never echo them. Leave server bind at 127.0.0.1 until private
  reachability is explicitly configured. Do not expose this server publicly.
- Codex mistakenly ran plain `uv sync` during launcher repair, removing 84
  optional third-party packages plus local Rust extension. Exact prior versions
  were restored and Rust rebuilt; tests and live runtime checks passed after.
  OpenJarvis editable distribution metadata advanced to current HEAD. No lock
  file change. Use direct venv Python or `uv run --frozen --no-sync` for checks.
- Authenticated fallback audio URLs and other media consumers may need a
  separately scoped audit. Do not remove authentication or inject secrets into
  URL query strings to solve them. Voice fallback redesign is not assigned.
- Waze voice pack, Siri Shortcut, Tailscale and real drive remain pending.
  M34 not started; M32 parked; unrelated Google APIs remain disabled by choice.

## Efficient continuation

Read AGENTS.md, ROADMAP.md and docs/m35-navigation.md once. For HANDOFF.md and
other long files, scoped rg + relevant regions only. Do not re-read just-edited
files. Batch independent checks; filter long output. Always `-p no:randomly`.
Verification is not the waste: reproduce before fixing, measure intermittent
failures repeatedly, and stop after two failed fixes to isolate the cause.
