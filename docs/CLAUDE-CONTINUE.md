# Claude Code continuation — 2026-09-05 (evening)

## State

M35 is done bar a real drive. Committed and pushed: HEAD `fb34814e` on
`feature/sage-customization`. The tree was clean at that commit.

Working end to end on the user's iPhone: "Hey Siri, drive home" and "Drive to
<place>" both reach `/v1/drive` over Tailscale, speak a briefing in Sage's
voice, and open Waze. Sage's Waze voice pack is live and installed.

## First task: pick up M34, unless the user says otherwise

M34 (self-diagnostics) has been scoped since 2026-09-02 and deferred five
times. Every defect found this week was found by the user noticing, never by
Sage reporting: an empty system overview, the wake word firing on a muted mic,
Spotify calling a full playlist empty, the dashboard reading zero all day.
Scope and rationale are in ROADMAP.md and the M34 memory file.

Smaller open items, none urgent:

- **A real drive** is the only unverified part of M35. Ask before assuming.
- **`jarvis` -> `sage` voice profile rename.** Deferred all session to avoid
  muddying debugging. It is why `/v1/drive` still takes `voice: "jarvis"`.
- **On 11 September**, check whether publishing the Google OAuth app actually
  removed the 7-day refresh-token expiry. Recorded as unproven. If it did not,
  Google access breaks again and the morning digest silently reports nothing.
- ~300 undeletable `jarvis-tts-*` temp dirs from an older bug. Needs a reboot.
  The code no longer creates them.

## Hard-won facts — do not re-litigate

- **Waze:** a prerecorded voice pack cannot speak street names; only voices
  marked "Including street names" can, and those are TTS. Waze will not
  auto-start from a deep link but starts itself after 3-5s, so the Shortcut
  opens Waze first and talks over the countdown. That ordering is also what
  stops Siri saying "Sorry, something went wrong" — audio must not play while
  Siri is on screen. The spoken briefing has a ~5s budget before Waze talks.
- **Siri:** "OK"/"Done" cannot be suppressed from inside a Shortcut. Only the
  global iOS Siri Responses setting, which is now off. `Play Sound` is not a
  Siri response and still plays.
- **Voice pack uploads:** run the uploader with `PYTHONIOENCODING=utf-8`. It
  prints a checkmark emoji that crashes a cp1252 console *after* the upload
  succeeded, losing the pack UUID. That already orphaned one public pack.
- **M32 is parked and should not be resumed by default.** Electron apps expose
  no useful automation tree; that is structural, not effort.

## Ground rules

- Never `git add -A` — there is a vendored CPython tree in the repo.
- Live config is external: `C:\AI\OpenJarvis-Data\config.toml`. Roadmap and
  data changes go there, not into repo code.
- Do not expose the server publicly. It holds Google tokens and runs code.
  Reachability is Tailscale only; that decision was explicit.
- Do not add an audio player, replay controls or sliders to the chat voice UX.
  Intended everyday behaviour is ephemeral streamed speech.
- Start and stop through the Start menu `Sage` / `Stop Sage` shortcuts.
- Checks: direct venv Python or `uv run --frozen --no-sync`. A plain `uv sync`
  prunes optional extras and has already broken this environment once.
- Always `-p no:randomly`; the failing set rotates under a shuffled order.
- `gh` is installed and logged in but **not on PATH**:
  `C:\Program Files\GitHub CLI\gh.exe`. Use `gh search code` to look for a
  working implementation before concluding something is a platform wall — that
  is exactly the mistake made with the Waze recorder.

## Efficient continuation

Read AGENTS.md, ROADMAP.md and docs/m35-navigation.md once. For HANDOFF.md and
other long files, scoped rg plus the relevant regions only. Do not re-read
just-edited files. Batch independent checks and filter long output.

Verification is not the waste: reproduce before fixing, measure intermittent
failures repeatedly, and stop after two failed fixes to isolate the cause.
