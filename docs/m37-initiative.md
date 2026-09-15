# M37 — Initiative: Sage starts a conversation

Scoped 2026-09-15. Not started.

## Decisions taken (2026-09-15)

- **Timed quiet silences everything unprompted except reminders the user
  set.** Initiative, greetings, welcome back and tell-me-when go quiet; a
  scheduled reminder still speaks. "Be quiet" means be quiet; an alarm is
  an alarm.
- **Busy, first cut: a full-screen app in front, or a meeting/call app in
  front or playing audio.** Recent keyboard or mouse input does *not* count
  -- the user chose to let Sage speak into a working lull, with the idle
  period (no chat turn for five minutes) as the only conversational gate.
  Music does not count either.
- **Long-term facts are allowed in every mode**, with the writer told to
  leave personal matters alone unless the user raised them recently. A
  per-fact "not for initiative" mark comes later, when a fact turns out to
  need it; the user prefers to add exclusions as they arise over a blanket
  rule.
- **Gentle is mostly questions about today's work, with an occasional
  useful nudge.** Nothing from outside the conversation in Gentle.

## Why

M36 gave Sage a sense of *now*: it knows when someone is at the desk and
speaks first on three fixed occasions. That still leaves it silent for the
hours in between. A person who shares a room with you will, now and then,
ask what you are working on, mention something they read, or suggest a
break -- and will also, often, decide that silence is better. Initiative is
that: Sage occasionally opening a conversation, with the judgement to not.

The user's framing, kept as the design centre: **the timer means "consider
speaking", not "speak no matter what".**

## What already exists (and is reused, not rebuilt)

| Need | Already in place |
| --- | --- |
| A controller that decides *whether Sage may speak* before any model call | `core/moments.py` `decide()` -- pure, presence-gated, quiet hours, "not now", daily caps |
| Delivery out loud, ducked, with a chime, recorded, appended to the chat | the M36 moment path end to end |
| Presence (is anyone there) | `PresenceMonitor`, 5 s poll, listener on change |
| Recent context: the day's turns, yesterday's episode, the profile | `memory/episodes.py`, `USER.md`, the trace store |
| Silencing by voice or text | `not_now` tool + Settings switch |
| Answering back by voice | wake word / continuous conversation, barge-in |

Initiative is therefore **a fourth moment kind with its own policy**, not a
second engine. What is genuinely new: a *busy* model (M36 explicitly left it
out), a *cadence* (idle period, cooldown, hourly cap), *follow-up*, *timed
quiet*, *modes*, and a prompt writer that is allowed to say nothing.

## Modes

One setting, `initiative_mode`, in `presence.json` under the M36 master
switch. Off means Sage behaves exactly as it does after M36.

| | Off | Gentle | Curious | Social |
| --- | --- | --- | --- | --- |
| Idle before considering | -- | 5 min | 4 min | 3 min |
| Cooldown between prompts | -- | 10 min | 7 min | 5 min |
| Cap per hour | -- | 3 | 5 | 8 |
| Categories | -- | contextual, useful | + curious, interesting | + reflective |
| Long-term memories | -- | yes, guarded | yes, guarded | yes |
| Follow-up if unanswered | -- | once, after 60 s | once | once |

Every number is a setting; the table is the defaults. **Gentle is the
recommended first version**, as the user proposed: five-minute idle, one
prompt per ten minutes, timed quiet.

## The controller

Runs inside the moment engine's tick (5 s). All of these must hold before
the model is even asked:

1. **Master switch on, mode not Off, not in quiet hours, not snoozed** --
   the existing M36 guards, unchanged.
2. **Present.** Presence gate as for every moment.
3. **Not busy.** New. Any of these means silence:
   - Sage is mid-turn: a chat completion in progress, a Flux turn open, a
     reply being spoken or streamed. (Server-visible: the request, the
     Flux proxy, the TTS stream.)
   - The user is talking to Sage: a chat turn in the last *idle period*.
   - A meeting or call: foreground window or an active audio session
     belonging to Teams, Zoom, Discord, Meet, Messenger (configurable list).
   - A full-screen app: foreground window covering its monitor (a film, a
     game). Same signal Windows uses for its own automatic Do Not Disturb.
4. **Cadence.** At least *idle period* since the last conversation turn
   and the last initiative; at least *cooldown* since the last initiative;
   fewer than *cap* in the last hour.
5. **Not waiting on an answer** to a previous prompt (see follow-up).

Only then is the model asked -- and it may answer with nothing.

## The prompt writer

gpt-5.6-luna, as the other moments, one call. Given:

- the mode and the categories it allows;
- the current conversation (the day's turns from the trace store, most
  recent first, capped);
- the recent episodes ("recent days");
- the profile (`USER.md`);
- long-term facts, minus the excluded ones -- see the memory rule;
- what Sage has said on its own initiative recently (the record), so it
  does not repeat a theme;
- the time of day, and how long the user has been at the desk without a
  break (from presence).

It is asked to pick a category, or to **decline**: the reply `SKIP` means
"nothing worth saying now", is recorded as such, and counts as a half
cooldown so a run of skips does not turn into a run of attempts. This is
the "silence is better" option, and it is a first-class outcome.

Categories, as the user drafted them, with the rule for each:

- **Contextual** -- about something *in the conversation today*. Memory may
  colour the phrasing, never supply the subject.
- **Useful** -- a break, water, the time, a class in an hour. Drawn from
  presence (time at desk) and the schedule.
- **Curious** -- a question in the user's field (the profile says civil
  engineering). No memory needed.
- **Interesting** -- a fact, same field or adjacent. No memory needed.
- **Reflective** -- an observation about how the user is working. Social
  only: it is the one most likely to feel like being watched.

The writer's own restraint is part of the prompt: one to two sentences,
one question at most, never two prompts on the same theme in a day, and
"if the conversation today is about something absorbing, do not interrupt
it with a fact."

## The memory rule

Long-term facts (`memory_facts.jsonl`) have no sensitivity tag today. The
user's decision is to allow them in every mode and add exclusions as they
arise rather than fence everything off. So:

- The writer sees the facts, with the instruction: *never open a subject
  from long-term memory that is personal -- relationships, health, money,
  family, anything the user would not expect a colleague to bring up --
  unless the user raised it themselves in the last few days. When in doubt,
  SKIP.*
- `presence.json` gains `initiative_excluded_facts`: substrings; any fact
  matching one is withheld from the writer. Empty to start. A fact that
  produces an unwelcome prompt goes on the list with one Settings entry,
  and the record of what was said makes it easy to see which fact it was.
- Gentle's *categories* still keep it to today's work: memory informs how
  a question is phrased, not what it is about.

## Follow-up and answering

An initiative prompt is spoken (chime, ducked) and appended to the chat as
Sage's turn, like every moment. An *answer* is any chat turn (typed or
spoken) within the follow-up window. If none arrives within 60 s, Sage
follows up **once**, shorter, in the same vein ("No rush -- I was only
curious.") or with something else, and then lets it go: the prompt is
marked unanswered, the cooldown runs from the follow-up, and nothing more
is said about it. Two unanswered prompts in a row double the cooldown for
the rest of the hour: being ignored is a signal.

Voice answers without the wake word: after an initiative prompt, if the
wake word or continuous conversation is on, the browser opens the
microphone for a short reply window (the same 8 s silence rule as after a
reply), so "yes, actually" works without "Hey Sage". Phase 2; the first
cut expects the wake word or typing.

## Quiet commands

`not_now` grows into a small vocabulary, still deterministic:

| Said | Effect |
| --- | --- |
| "Not now" / "quiet today" | rest of the day (as now) |
| "Be quiet for 30 minutes" | timed quiet, any duration; default 30 min if unstated |
| "Stop talking for now" | timed quiet, 30 min |
| "Continue" / "you can talk again" | lifts the quiet |
| "Only speak when I call you" | initiative mode Off (stays off until Settings or "you can start conversations again") |

Timed quiet silences every unprompted moment -- initiative, greeting,
welcome back, tell-me-when -- but not a reminder the user scheduled.

## Settings

Under Presence in Settings: mode (Off / Gentle / Curious / Social), idle
period, cooldown, cap per hour, follow-up on/off, the busy-app list, and
"may use long-term memories" (forced on in Social, off otherwise, editable
later). The Health page's Moments line reports the mode and the last
initiative. The Logs page shows each prompt, skip, answer and follow-up.

## Phases

1. **Controller + Gentle.** Busy model (Sage mid-turn, full-screen,
   meeting apps), cadence, the writer with SKIP, contextual +
   useful categories, timed quiet and mode commands, Settings, tests with
   the fake clock like M36's. Verified by living with it for a day.
2. **Follow-up and answers.** Answer detection, the single follow-up, the
   ignored-twice back-off, the reply-listening window in the browser.
3. **Curious and Social.** The other categories, the memory rule's Social
   branch, theme de-duplication across days.

## Out of scope

- Any prompt that is a health or fault warning. Pull-only stands (M34).
- Reading screen content for prompts. Window titles and audio sessions
  only; the camera stays deferred.
- Sage initiating while the master presence switch is off.
- A "personality" beyond the existing persona: Initiative changes *when*
  Sage speaks and *whether*, not who it is.

## Risks

- **Annoyance is the failure mode, not silence.** Every default errs
  quiet; the SKIP path and the ignored-twice back-off exist for this.
- **Busy detection is heuristic.** A film in a window, a call in a
  browser tab, a game in borderless mode can slip past. The quiet
  commands are the backstop, and the first day will find the gaps.
- **Cost.** One cloud call per considered prompt, at most a few an hour
  in Gentle; SKIPs still cost a call. Acceptable; noted.
