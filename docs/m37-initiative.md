# M37 — Initiative: Sage starts a conversation

Scoped 2026-09-15. Not started.

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
| Long-term memories | -- | none | none | yes |
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
   - The user is typing or clicking right now: input in the last 20 s.
     Initiative is for the lull, not the middle of a sentence.
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
- **long-term facts only in Social** -- see the memory rule;
- what Sage has said on its own initiative recently (the record), so it
  does not repeat a theme;
- the time of day, and how long the user has been at the desk without a
  break (from presence).

It is asked to pick a category, or to **decline**: the reply `SKIP` means
"nothing worth saying now", is recorded as such, and counts as a half
cooldown so a run of skips does not turn into a run of attempts. This is
the "silence is better" option, and it is a first-class outcome.

Categories, as the user drafted them, with the rule for each:

- **Contextual** -- about something *in the conversation today*. Never
  from long-term memory outside Social.
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

Long-term facts (`memory_facts.jsonl`) have no sensitivity tag today, and
adding one to 500 existing facts is a project of its own. So the v1 rule
is structural rather than judged: **Gentle and Curious never see long-term
facts at all** -- only today's conversation, the episodes, and the
profile. Social does. The example the user gave ("that person you liked
four years ago") therefore cannot happen outside Social, and in Social the
prompt still says to avoid anything the user has not raised themselves in
the last few days. A later version can add a `sensitive` tag to facts and
let Curious use the rest.

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

Whether timed quiet silences *only* initiative or every unprompted moment
(greeting, welcome back, tell-me-when) is a decision to take; the
recommendation is everything, because "be quiet" means be quiet.

## Settings

Under Presence in Settings: mode (Off / Gentle / Curious / Social), idle
period, cooldown, cap per hour, follow-up on/off, the busy-app list, and
"may use long-term memories" (forced on in Social, off otherwise, editable
later). The Health page's Moments line reports the mode and the last
initiative. The Logs page shows each prompt, skip, answer and follow-up.

## Phases

1. **Controller + Gentle.** Busy model (Sage mid-turn, recent input,
   full-screen, meeting apps), cadence, the writer with SKIP, contextual +
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
