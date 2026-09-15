# M36 — Presence: Sage knows when to speak

Scoped 2026-09-13. Phases 1 and 2 implemented 2026-09-13; phase 3 on
2026-09-15.

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

**Status (2026-09-13):** implemented. `memory/episodes.py` reads the day's
turns from the trace store -- only the chat agents, and excluding any query
that matches a scheduled task's prompt, because a job on the orchestrator
agent leaves a trace indistinguishable from a typed message.
`agents/episode_writer.py` is a registered agent on a self-registering
nightly cron (23:00 local, converted to UTC from `proactive.timezone`; the
first registration fell back to UTC and landed at 07:00 because
`[scheduler]` has no timezone field). Recent days ride the existing
`inject_context` path beside facts, so there is one place a prompt gains
memory. Settings sit under the presence master switch in `presence.json`.
Verified live: the job wrote a real, specific entry for the day from 74
turns, and Sage answered "what were we working on yesterday" from it
through the chat path. The digest's "since we last spoke" line is deferred
to phase 3, where it belongs with the good-morning moment.

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

**Decisions taken before building (2026-09-15):** all three moments in this
phase; the words are model-written by gpt-5.6-luna from real context, with a
fixed line as the fallback so a cloud failure never silences a moment; the
welcome-back threshold is **one hour**; "not now" is both a tool the model
calls (`not_now`, deterministic -- no judgement about whether the user meant
it) and a switch in Settings.

**Status (2026-09-15):** implemented. `core/moments.py` holds the state
(`OPENJARVIS_DATA/moments.json`: today's snooze, the last presence reading,
the last absence answered, per-kind daily counts, pending watches, and the
record of what was said), a pure `decide()` over the presence snapshot, the
context builders, the model call, the server-side voice (Cartesia synthesis
into the shared `speech/player.py`, which `jarvis digest` now uses too), and
the polling `MomentEngine`, wired in `serve.py` beside the monitor.

How the two greetings tell a real return from noise: **welcome back** needs
an absence the monitor itself watched begin and end (so a server restart,
during which nothing was watched, is never a "return"); **good morning**
also accepts a gap in the engine's own readings, because the night is
exactly such a gap. One return earns one greeting; the absence is marked
answered either way, so a coffee break is not greeted later when a longer
absence would have been. The absence length reported includes the idle
threshold, since the monitor notices an empty desk five minutes late.

**Told on request** is `tell_me_when`: the model resolves a local time (it
has the class schedule and the clock) or names a scheduled task id, and the
engine speaks when it is due -- held while away or in quiet hours, dropped
with a note after twelve hours undelivered, because "your class started
yesterday" helps nobody.

Every moment is appended to the open web conversation as an assistant turn
labelled "Sage said this aloud" (`hooks/useMomentsFeed.ts` polls
`GET /v1/presence/moments` with a watermark), so the next reply knows it was
said and the transcript reads as what happened. Settings gained the
per-moment switches, quiet hours, today's "not now", pending watches and the
last things said; the Health page gained a Moments line.

Verified live (2026-09-15): "Tell me when it is 12:56" through the real
chat path registered a watch; the user was away at 12:56 so it was held,
and on their return at 12:59 it was spoken through the speakers, worded by
the cloud model ("three minutes past the time you asked to be told").

**Revised for a machine that is not on all day (2026-09-15).** Sage is shut
down at night and for outings, which changed three things: the morning
greeting became a **daily greeting** named for the time of day (first
appearance Sage sees, once per local calendar day); a **gap in the engine's
own readings of an hour or more counts as a return** for welcome back too,
because Sage being off is how most absences look (a restart under an hour
stays silent); and **missed episodes are written on boot** -- the 23:00 cron
only runs when Sage is up at 23:00, so the engine's startup hook writes any
past day with conversations and no entry through the same agent before the
first greeting is composed.

**Audio ducking (2026-09-15).** Everything the server speaks -- moments,
scheduled reminders, class alerts, the briefing -- now holds every other
app's mixer volume at 35% of its own level while the voice plays, with a
300 ms fade each way, and restores each exactly (`speech/ducking.py`, via
`pycaw`; a no-op without it). Web-UI replies are excluded on purpose: a
browser is one audio session, so lowering the YouTube tab would lower the
reply in the Sage tab. Verified against a tone in a second process: 1.0 →
0.35 for the sentence → 1.0.

## Phase 4 — Texture

Cheap, high felt impact, no intelligence involved:

- A spoken "one moment" when a tool call has run longer than a few seconds,
  using the M30 acknowledgement-clip mechanism.
- Greetings that vary and name the time of day.
- The orb's idle state reflecting `presence`.

**Status (2026-09-15):** all four pieces implemented. The "one moment"
filler plays after five seconds with nothing said back, generating or
waiting on a tool alike, holds the answer's speech until the clip ends, and
repeats every twenty seconds of silence; clips are pre-rendered by
`scripts/generate_greetings.py` and never claim `audioPlaying` (an
invariant pins that). The orb has an `away` state -- standing by at 60%
brightness and 85% size. Fallback lines rotate and the model is told how it
opened its last three moments. A soft two-note chime (`speech/chime.py`,
synthesised, not shipped) precedes every unprompted moment inside the same
ducking window as the voice.

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
