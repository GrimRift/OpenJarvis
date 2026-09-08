# M34 v2 — health checks that check whether things work

Scoped 2026-09-08. Not started.

## Why there is a v2

v1 shipped 49 checks across six sections, one shared implementation, a fix
confirmation flow and an invariant preventing a fourth surface. It is a real
foundation. It is also **not a system health check yet**, and the gap is
specific rather than vague.

The roadmap named five failures as the case for M34. Measured against the
checks v1 actually produces:

| Motivating failure | Caught by v1? |
| --- | --- |
| Google OAuth token expired silently | yes — token age |
| Three Google APIs disabled at project level, 403 for months | **no** |
| Briefing's `world` section collected nothing, ever | **no** |
| Dashboard read zero for energy, tokens and requests all day | **no** |
| Wake word fired on a muted microphone | **no** |

One of five. The reason is a single design flaw running through all of it:
**almost every v1 check asks whether configuration is present, not whether the
thing works.** A disabled Google API has a perfect credential file. A digest
section with no sources parses cleanly. An unwrapped engine records no
telemetry while every config value reads correctly.

`live` does not close this. It is used in exactly three places, all engine
related: it probes cloud engine reachability and lists models. One extra check
out of forty-nine. The name promises a great deal more than it delivers.

Two further blind spots, neither touched by v1:

- **42 tools are enabled and none are checked.** A tool whose module fails to
  import is silently absent from the registry. That is this codebase's
  signature failure mode and nothing notices it.
- **Voice is three shallow checks** — backend loads, model file exists, a key
  is present.

## Decisions taken (2026-09-08)

- **Live checks make one minimal real call per provider.** Free metadata
  endpoints were offered and rejected: they cannot catch quota exhaustion or a
  key that authenticates but fails on use. Each live run therefore costs a
  little and consumes Google's 30/day caps. This is the user's explicit
  choice, made knowing the cost.
- **Voice checks are split across both surfaces.** The muted-microphone
  failure happened in the browser, where capture lives; no server-side check
  can see it. The server checks backends, keys and Flux reachability. The
  Health page additionally reports microphone devices and permission state,
  which only the browser can see. A server-side audio library was considered
  and rejected — it would see the machine's devices but not the browser
  permission that actually gated capture.
- **Tools are checked by registration and dependencies, never by invocation.**
  Invoking even a "safe" subset means defining safe, and a read path that
  quietly wrote has already cost this project a day of missed reminders.
- **Order: feature wiring and provider checks first**, then tools, then voice
  depth.

## Phase 1 — feature wiring

Targets three of the five motivating failures directly. All local, no cost.

- **Digest sections resolve to real sources.** `MorningDigest._resolve_sources`
  reads `section_sources.get(section, default_map.get(section, []))`, so a
  section configured with an empty list returns that empty list and the
  default map is never consulted. That is exactly how `world` stayed enabled
  and silent for the entire life of the feature. The check compares every
  enabled section against its resolved sources and fails on any that resolve
  to nothing.
- **Telemetry is actually recording.** The dashboard read zero because only
  the primary engine was wrapped in `InstrumentedEngine` while discovered and
  cloud engines went in raw. The check inspects the live engines and fails if
  a configured one is unwrapped. Needs the running app state, so
  `run_health_checks` gains an optional `app_state` parameter — and per the
  standing trap, `serve.py` must be wired separately from `SystemBuilder`.
- **The scheduler is alive.** `TaskScheduler._thread.is_alive()`. v1 reports
  each job's last run but never asks whether the poll loop is still running,
  so a dead scheduler currently reports six healthy jobs.
- **Telemetry freshness.** Last recorded call older than the process start
  with requests having been served is a fault, not a zero.

## Phase 2 — providers that are actually exercised

Live only. Each is one minimal real call, and each is marked `live` so the
report says what it spent.

| Provider | Minimal call | Catches |
| --- | --- | --- |
| Google (Gmail, Calendar) | smallest possible list, `maxResults=1` | the 403 disabled-API class |
| Google Routes / Places | one trivial request | quota exhaustion, billing state |
| Deepgram | key check plus a socket open against Flux | the DNS fault that has bitten before |
| Cartesia | one-word synthesis | paid key valid, voice id still exists |
| Tavily | one-word search | key valid, monthly limit not hit |

Google is the priority: three APIs returned 403 for months and were diagnosed
as a scope problem for just as long. Only a real call distinguishes those.

**Cost note.** Routes and Places share a 30/day cap. A live run spends two of
those. The page must say so before the button is pressed, not after.

## Phase 3 — tools

- Every tool in `[agent] tools` is present in `ToolRegistry`. A name in config
  that never registered is a silent capability loss.
- Every registered tool's module imports cleanly.
- Tools with directory or credential requirements have them: `coding_command`
  and the file tools against `OPENJARVIS_*_DIRS`, connector-backed tools
  against their credential files.
- Report tools that are registered but not enabled, as information rather than
  a fault.

Never invoked.

## Phase 4 — voice depth

- **Server:** Flux reachability, TTS key validity (covered by phase 2's
  Cartesia call), wake-word model file, configured device versus what the
  speech backend actually loaded.
- **Browser:** `navigator.mediaDevices.enumerateDevices()` for microphone
  presence, and the permission state. This is the only place the muted or
  denied microphone is visible.

The browser half reports into the same Health page but cannot come from
`run_health_checks`, which is server-side. It must be presented as clearly
client-side rather than merged into the server report as though it were the
same kind of evidence.

## Out of scope

- Applying fixes for anything discovered here beyond the existing operational
  set. Source edits remain M33.
- Proactive alerting. Pull-only stands.

## Open questions

- Where the Deepgram key lives. `flux.py` reads `DEEPGRAM_API_KEY` from the
  environment and it is not visible in a plain shell, so the check must not
  assume the process environment matches the user's.
- Whether a live run should be rate limited in code, given the caps it spends.
