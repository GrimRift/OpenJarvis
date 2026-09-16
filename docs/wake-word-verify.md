# Wake word, verified by transcript

Status: **phase 1 built 2026-09-16**, on by default (`wake_word_verify = "local"`), measured on the recorded samples below. Phase 2 (a day of Voice-log reading) next.

## Why

The wake word fires on loud audio that is not the phrase. The chain today
-- openWakeWord model, custom verifier, score > 0.79 on two consecutive
80 ms frames, a warm-up guard, a silence floor
(`speech/wake_word.py`, `hooks/useWakeWord.ts`) -- judges acoustic shape in
a 1.28 s window, and nothing in it can tell a loud transient of the right
length from the name. More training samples move the boundary; they do not
add the missing signal, which is the words.

## Design

A **second stage that transcribes**, on the server, inside the wake-word
socket handler that already receives every 80 ms PCM frame:

1. Keep a ring of the last 2.0 s of frames (25 frames, 64 KB; nothing is
   written to disk).
2. On a detection, do not tell the browser yet. Transcribe the ring with
   the configured verifier backend.
3. Confirm only if the transcript contains the wake phrase, matched
   loosely: any of `sage, stage, sayge, saige, sage's` optionally preceded
   by `hey, hi, hay, he, a, eh, ok`. STT mishears the name in the same few
   ways every time; the list is written down and tested, not learned.
4. Confirmed: send `{detected, verified: true, heard: "hey sage"}`;
   the browser behaves exactly as now. Rejected: send
   `{rejected, heard: "..."}`, trace `wakeword.rejected {heard}`, and put
   one line in the Voice log ("Ignored a wake-word-like sound: heard
   'the stage'"), so every false trigger is readable afterwards.
5. An STT failure (network, timeout > 1.5 s) **confirms** -- the detector's
   own verdict stands, as today. Verification can only remove firings,
   never add them, and never makes the wake word deaf when the cloud is
   down.

### Verifier backend

`[speech] wake_word_verify = "deepgram" | "local" | "off"` in
`config.toml`. Deepgram prerecorded (nova-3, ~0.3-0.6 s for 2 s of audio)
is the recommendation: it is the engine already trusted for every turn,
and one two-second clip per firing is a rounding error on the bill.
`local` uses the configured faster-whisper model (0.5-1 s on CPU for the
`base` model; private). `off` restores today's behaviour.

### What it costs

The greeting ("Yes, Sir?") and the microphone open after the transcript
comes back: about half a second later than today with Deepgram, up to a
second with local. Speaking right after the phrase loses nothing -- the
turn opens from the ring's end, so audio in the gap is still captured --
but the acknowledgement lands later. This is the trade; the open question
below is whether to hide it.

### Optional, later

- **Loudness normalisation** before scoring (slow AGC on the server), so
  "loud" alone cannot stand in for the phrase. Cheap; uncertain gain;
  only worth trying if verification leaves a residue of false firings that
  transcribe to the name.
- **Keep rejected clips** (2 s WAVs) in `OpenJarvis-Data/wake-word/rejected/`
  so that, if a verifier retrain is ever wanted, the negatives already
  exist. Off unless asked; the user does not want to collect samples now.

## Phases

1. Server: ring buffer, verifier call, fuzzy match (pure, tested with the
   misheard spellings), the confirm/reject messages, fail-open on error.
   Config key. Browser: handle `rejected` (trace + Voice log), ignore
   nothing else.
2. Live: a day with the Voice log; every "Ignored a wake-word-like sound"
   line is a false trigger that would have fired today.

## Web parity

Web is the only client of the wake-word socket; the desktop app inherits.
One server change, one message type added, no UI beyond a log line.

## Decisions and measurements (2026-09-16)

- Verifier: **local faster-whisper** (distil-large-v3.5 on CUDA, as
  configured). Deepgram was offered and declined; no cloud in the loop.
- Latency accepted: the greeting waits for the transcript. Measured over
  the recorded samples: median 385-430 ms, p90 ~480 ms, 2.2-2.4 s once
  for the first call after a restart (model load; the serve warm-up
  covers this in practice).
- "sage" alone does **not** confirm; "hey sage" or its shape is required.
- Matching is two rules, either suffices: the word lists, or a phonetic
  tail on the run-together letters (`[a-z]{1,4}s[aeiy]+…$`), because
  Whisper wrote the phrase as "A sage", "He's in", "Hey, see you", "He
  said", "Be sage", "Easy", "Haysage", "He sees", "A seed" across the
  recordings. Every shape it used is a test case.
- The clip is level-normalised to a 20,000 peak before transcription:
  Whisper's VAD hears nothing in quiet audio.
- **On the 189 recorded positives and 102 negatives**
  (`OpenJarvis-Data/wake_word_samples`): the two 23 August browser
  sessions (63 clips, peaks 223 and 2,549) contain no intelligible
  speech -- Whisper returns its silence hallucination "you"/"Thank you"
  even with VAD off -- and were excluded. On the rest: **114 / 126
  positives confirmed (90 %)**; **98 / 102 negatives rejected**, and all
  four that passed contain the phrase in their transcript ("best song.
  Hey Sage.") -- mislabelled captures, not failures. The 12 misses are
  quiet or fast utterances Whisper heard as other words ("change",
  "Peace", "I see you soon") and two where it heard "sage" alone.
- So the expected live effect: a loud noise that transcribes to nothing
  or to other words never wakes Sage; roughly one in ten quiet or hurried
  "Hey Sage" is also lost and must be repeated. Each rejection is a
  Voice-log line with what was heard.

## Open questions

1. After a day of Voice-log lines: is the one-in-ten miss on quiet/fast
   says acceptable, or should "sage" alone confirm (recovers 2 of the 12
   recorded misses)?
