# M36 — Presence: Sage knows when to speak

Scoped 2026-09-13. Phase 1 implemented 2026-09-13.

## Why

Sage's entire unprompted life today is two scheduled moments: the 05:00
briefing and the nightly inbox triage to Telegram. Everything else waits to be
asked. It has no sense of *now* -- whether anyone is at the desk, what happened
since the last conversation, or that a good moment to say something has
arrived. That is why a very capable system can still feel like a vending
machine.

The organising idea: **Sage should know when to speak, not just what.**

## Decisions taken (2026-09-13)

All answered by the user; none are open.

- **Sage may speak first.** Greetings, welcome-backs, and things the user
  asked to be told about. **Never health warnings** -- the 2 September
  decision that diagnostics are pull-only stands untouched. Each kind of
  moment is its own switch.
- **The whole feature has one master switch in Settings.** Off means Sage
  behaves exactly as it does today. The per-moment switches sit beneath it.
- **Delivery is out loud through the PC speakers, only.** No chat message, no
  Windows toast, no Telegram for these moments.
- **"Busy" is not modelled in v1.** The only gate is presence: is someone at
  the desk. No fullscreen, call or typing detection. Sage speaks when the
  user is present and it has a reason.
- **Presence may use idle time, window titles and screen content** --
  anything that shows someone is there. **Not the camera.** That is a later
  addition, explicitly deferred, not rejected.
- **The daily episode summary is written by gpt-5.6-luna**, `engine = "cloud"`
  pinned the same way the digest and scheduler pin it. One call a night.

## A constraint found before starting

The server cannot play audio in the background on this machine. The existing
`_play_audio` in `digest_cmd.py` tries `ffplay` (not installed) and falls back
to `os.startfile`, which opens a visible media player window. Sage's voice
reaches the speakers today only through the browser tab, which synthesises
and plays.

Unprompted speech cannot depend on a tab being open. **Phase 3 therefore adds
a server-side player**: `winsound.PlaySound` from the standard library plays
WAV with no window and no new dependency, and Cartesia can return WAV
directly (the streaming path already delivers PCM). The browser path is
untouched; it remains how replies are spoken. This is the one piece of
genuinely new plumbing in the milestone.

Because delivery is server-side, **the master switch and per-moment switches
must live server-side too**, edited from the Settings page through an API,
not in the browser's localStorage where the other UI settings live. A switch
the server cannot see would be a switch that does nothing.

**Revised while building phase 1:** the switches live in
`OPENJARVIS_DATA/presence.json`, not a `[presence]` section in `config.toml`.
The server has no safe way to rewrite the hand-commented TOML -- the only
precedent is a line-by-line rewrite, and one malformed line there breaks
every credential -- while a sidecar file is re-read on every poll, so the
switch is live at once with no restart.

**Also revised:** `ffplay` was installed on 2026-09-13 (`Gyan.FFmpeg` via
winget) and verified to play a Sage clip with no window. The existing
`_play_audio` already tries it first, so phase 3 needs no new player.

## Phase 1 — Presence sensing

Produces one thing and changes nothing visible: a `presence` state of
`away` or `present`, refreshed on a short interval, that every later phase
consults.

- **Idle time.** `GetLastInputInfo` through `ctypes`; no dependency. Present
  if input in the last few minutes.
- **Foreground window title.** `list_windows` already exists. Recorded as
  evidence of activity, not judged as busy.
- **Screen content** is permitted but not needed for v1; idle time answers
  the question. Kept in scope so a later phase can use it without a new
  decision.
- Exposed as `GET /v1/presence` and a `presence` line in the Health page, so
  the user can see what Sage believes and why before anything speaks.

Verification is not a unit test. It is walking away from the desk and watching
the state change, then coming back.

**Status (2026-09-13):** implemented. `core/presence.py` holds the sensors,
the pure decision, and a polling monitor with an injectable clock; it is
wired in `serve.py` (not `SystemBuilder`, per the standing trap) and exposed
as `GET /v1/presence`, `GET/PUT /v1/presence/settings`, a Presence section
in Settings, and a line under Features on the Health page. Verified live:
the switch flips the state within one poll in both directions, and the page
reads "present (input 61s ago, in front: Sage - Opera)". The away
transition needs five minutes without input, which only the user can supply.

## Phase 2 — Episodes: memory that spans days

Memory today is 500 durable facts, re-injected into every prompt. Good for
"prefers metric", useless for "on Tuesday you were debugging the Waze pack".

- A nightly job writes a short summary of the day's conversations to an
  **episode store** beside the fact store, keyed by date. Written by
  gpt-5.6-luna, same scheduling pattern as the digest.
- The last few episodes are available to the model as "recent days", so
  "what were we working on yesterday" has a true answer.
- The morning briefing gains a "since we last spoke" line drawn from the
  episode, not the inbox.
- This is the rolling summary M30 deferred, built for a different reason:
  **recall, not context compression.** The 8,000-token window stays as it is.

Nothing here speaks. It is the substance the moments in phase 3 draw on.

## Phase 3 — Moments

Event-driven, not schedule-driven. Three kinds in v1, each its own switch
under the master:

| Moment | Trigger | Draws on |
| --- | --- | --- |
| Good morning | first `present` after the day's first hour of absence | phase 2 episode, today's calendar |
| Welcome back | `present` after a long `away` (threshold configurable, hours not minutes) | anything that finished while away |
| Told-on-request | the user said "tell me when X" and X happened | scheduled jobs, calendar |

Guards on every moment, none of them optional:

- **Presence gate.** Never fires while `away`. Talking to an empty room is
  the failure this milestone exists to avoid.
- **Daily cap per moment.** One good morning a day, however many times the
  presence state flickers.
- **Quiet hours**, configurable, default late night.
- **"Not now"**, spoken or typed, suppresses all moments for the rest of the
  day.

Speech goes through the server-side player from the constraint above. Every
moment is written to the conversation transcript as well, so what Sage said
unprompted is visible afterwards, not only heard once.

## Phase 4 — Texture

Cheap, high felt impact, no intelligence involved:

- A spoken "one moment" when a tool call has run longer than a few seconds,
  using the M30 acknowledgement-clip mechanism.
- Greetings that vary and name the time of day.
- The orb's idle state reflecting `presence`.

## Out of scope

- Camera-based presence. Deferred to a later milestone by explicit decision.
- Health or fault warnings spoken first. Pull-only stands.
- Any delivery other than the speakers.
- Modelling "busy". A first version that speaks only when someone is present
  is enough to learn from.

## Order

Phases 1 and 2 first and together: they are invisible, they are the substrate,
and they can be verified on their own. Phase 3 only once presence is trusted --
if the state is wrong, every moment is wrong. Phase 4 last, or alongside 3.

## Relation to other milestones

M33 (self-improvement) and M29 (mobile) are queued. M36 should precede M29:
presence and episodes are what a phone client would want to inherit, and
building the phone first means building both twice.
