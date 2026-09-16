# Barge-in v2 — interrupt by speaking, without false cuts

Status: **phase 1 built 2026-09-16** (duck on StartOfTurn, 3 s candidate window, per-word confidences forwarded, confident stop-word fast path; 2-word rule kept). Phases 2-3 not started. Supersedes the first barge-in
(cut on the second transcribed word; see `frontend/src/lib/barge-in.ts`).

## Why

The first barge-in has one rule: while a voice reply plays, two transcribed
words cut it. It is fast and it is right most of the time, but it has no
middle ground. A cough plus a syllable of echo, two words from someone
else in the room, or the TV can reach two words, and when they do the
reply is gone -- there is no way to take a cut back. The fix is to make
the cut a *decision* reached through stages, and to protect the reply
while the decision is being made rather than after.

## What exists today (the parts v2 builds on)

| Stage | Today | Where |
|---|---|---|
| Sound detection | not used for barge-in | `lib/speech-analyser.ts` (RMS, for the orb) |
| Speech detection | Deepgram Flux `StartOfTurn` | `hooks/useFluxSpeech.ts` |
| Transcription | Flux `Update` partials; per-word confidences parsed server-side but **not forwarded** | `speech/flux.py:68`, `server/flux_routes.py:304` |
| Decision | `shouldInterrupt`: ≥ 2 words while `audioPlaying` | `lib/barge-in.ts` |
| Echo handling | `isEchoTurn`: a turn that ended under the threshold is dropped | `lib/barge-in.ts`, `InputArea.tsx:1420` |
| Stop-only | `isStopCommand`: "stop"/"enough" alone is consumed, not sent | `lib/barge-in.ts` |
| Reply playback | Web Audio with a `GainNode` per stream (unused for level) | `hooks/useStreamingTts.ts:153` |
| Logging | `voiceTrace('barge.*')` + the Voice log | `lib/voice-trace.ts` |

Two facts shape the design. Sage knows the exact text it is speaking, so
an "interruption" whose words are a run of that text is echo and can be
rejected by comparison, not by guessing. And the reply is a gain node
away from being ducked, so the reply can be lowered the instant speech is
detected and brought back if the speech turns out to be nothing -- which
also makes the browser's echo cancellation work better while the user is
actually talking.

## Design

### 1. Protect playback first

On `StartOfTurn` while a voice reply is playing: duck Sage's gain to 35 %
over 150 ms (the same level the server uses to duck other apps). Nothing
stops. If the candidate is rejected, the gain returns to 100 % over
300 ms. If it is confirmed, the stream stops as it does today. A cut that
was wrong now costs the listener half a second at low volume, not the
reply.

### 2. Four stages, kept apart

- **Sound**: not a decision input. The RMS analyser stays for the orb.
- **Speech**: Flux `StartOfTurn` opens a *candidate*. Flux's own
  endpointing is the speech detector; no second VAD.
- **Transcription**: Flux `Update` events feed the candidate: transcript,
  `audio_window_start/end` (speech duration), and -- new -- `words` with
  per-word confidence, forwarded by `flux_routes.py`.
- **Decision**: a pure reducer in `lib/barge-in.ts` sees every event and
  returns the next state plus effects (`duck`, `restore`, `stop`,
  `consume`). Nothing in `InputArea.tsx` decides anything.

### 3. Buffer possible interruptions

A candidate lives for a **confirmation window** (mode-dependent, 2.5 s
default) from `StartOfTurn`. Within it, each `Update` is judged; if none
confirms before the window closes -- or the turn ends first -- the
candidate is rejected, the gain restored, and the turn's final transcript
discarded as echo/noise exactly the way `isEchoTurn` drops it now.

### 4. Decision rules (conservative by default)

A candidate is **confirmed** when any one holds:

- **Command fast path**: a stop word -- `stop`, `wait`, `hold on`, `hang
  on`, `pause`, `enough`, `sage` -- appears as a confident word
  (≥ 0.8). One word is enough; that is what the words are for.
- **Enough speech**: word count ≥ *N*, speech duration ≥ *D*, mean word
  confidence ≥ *C*, and the words are not a run of Sage's own text.

Mode table (initial values, tuned in phase 3):

| Mode | N words | D speech | C confidence | window |
|---|---|---|---|---|
| Conservative (default) | 3 | 600 ms | 0.70 | 3.0 s |
| Balanced | 2 | 400 ms | 0.60 | 2.5 s |
| Sensitive | 2, or 1 at ≥ 0.85 | 250 ms | 0.50 | 2.0 s |

Today's rule is roughly Balanced without the confidence and echo checks.

### 5. Unreliable input, rejected outright

- **Echo**: the candidate's words, in order, occur in the last ~15 s of
  the text Sage has spoken (the TTS hook already has it sentence by
  sentence). Rejected regardless of count.
- **Low confidence**: any candidate whose mean word confidence is below
  *C*; isolated words below 0.5 are not counted at all.
- **Garbled**: words with no letters, or a transcript that is one token
  repeated.
- **Stale**: any event whose `turn_index` is not the candidate's, or that
  arrives after playback ended (those go down the normal turn path).
- **Side conversation**: no reliable signal short of speaker ID; the
  Conservative thresholds (3 confident words in 600 ms+) plus the echo
  and confidence rules are the defence. Recorded as a known limit.

### 6. State machine

```
Playing ──StartOfTurn──▶ Candidate ──Update(confirms)──▶ Interrupted
   ▲                        │  (duck)                        │ (stop)
   │                        ├─Update(rejects)/EndOfTurn/──▶ Playing (restore)
   │                        │  window expires
   └────playbackEnded───────┘
Interrupted ──EndOfTurn──▶ stop-only? consume : send as the next turn
```

Implemented as `reduce(state, event, now) -> { state, effects }` with
events `playbackStarted(text)`, `spokenText(delta)`, `playbackEnded`,
`startOfTurn`, `update{transcript, words, window}`, `endOfTurn`,
`turnResumed`, `tick`. `InputArea.tsx` becomes a thin adapter that feeds
events and runs effects; `shouldInterrupt`/`isEchoTurn` are retired,
`isStopCommand` stays for the consume decision.

### 7. Logging and test modes

- Every transition traced: `barge.candidate`, `barge.confirmed {reason,
  words, ms, conf}`, `barge.rejected {reason}`, `barge.restored`. The
  Voice log shows confirmed and rejected with the reason, so "why did it
  cut" and "why didn't it" are both answerable from the page.
- **Rehearsal mode** (Settings switch): the reducer runs and logs every
  decision but the `stop` effect is not executed -- only the duck. Used to
  tune against: background noise, music, a nearby conversation, garbled
  speech, genuine commands.
- **Fixture tests**: Flux event sequences captured from the trace replay
  through the reducer in vitest, one fixture per case above, asserting
  the decision and its reason.

### 8. Sensitivity

Conservative / Balanced / Sensitive as a Settings choice next to the
existing barge-in switch; ships Conservative. Thresholds live in one
table so tuning is a number change with a fixture test beside it.

## Phases

**Phase 1 — protect and buffer** (small)
- `useStreamingTts`: `duck()`/`restore()` on the existing gain node.
- `flux_routes.py`: forward `words`; `useFluxSpeech` types gain `words`.
- Duck on `StartOfTurn` while speaking; restore on reject; a confirmation
  window with a deadline. Existing 2-word rule kept as the decision for
  now, so behaviour changes only by getting softer.

**Phase 2 — decide** (the substance)
- The reducer with the mode table, echo-by-text, confidence, garbled and
  stale rules; the state machine; `InputArea` reduced to an adapter.
- Voice-log reasons; fixture tests for every rule.

**Phase 3 — test and tune**
- Rehearsal mode, Settings mode choice, live tuning session against the
  five cases, record the thresholds that held.

## Web parity

All of it is Web (the browser owns playback and the microphone); one
server change (forward `words`). Desktop/mobile inherit through the same
frontend.

## Decisions (phase 1, 2026-09-16)

- Duck to 35 % (150 ms in, 300 ms out), the server's level.
- Candidate window 3 s from `StartOfTurn`.
- Stop-word fast path ships in phase 1: one word of `stop / wait / hold
  on / hang on / pause / enough` at Deepgram confidence >= 0.8 cuts at
  once. `sage` alone does not; it is said mid-sentence too often.
- A stop-word cut whose whole turn is stop vocabulary is consumed, not
  sent (`isStopCommand` gained the same words).

## Open questions (before phase 2)

1. Ship Conservative as the default (3 words), accepting that "actually I
   meant" is not confirmed until the third word?
2. Rehearsal mode: a Settings switch, or a query flag on the dev server
   only?

## Known limits

- Someone else in the room addressing the user, at speech, confidently,
  for three words, will cut the reply. Only speaker recognition fixes
  that; out of scope.
- Ducking Sage's own reply lowers what the user hears of it during the
  window; a rejected candidate means half a second at 35 %.
