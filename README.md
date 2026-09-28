# Sage

**A personal, voice-first AI assistant for Windows.** Say "Hey Sage", ask, and
Sage answers out loud in a calm, JARVIS-style voice, using your mail, calendar,
music, maps and the web on your behalf. It runs on your own PC: the server, the
speech pipeline and the voices are local; cloud models and services are used
where you configure them.

Sage is built on [OpenJarvis](https://github.com/open-jarvis/OpenJarvis) and
keeps its Apache 2.0 license. It is a personal fork that has grown its own
voice, interface, tools and behaviour on top of that foundation. See
[Acknowledgements](#acknowledgements).

> **Status:** Sage is developed and used daily on one Windows 11 laptop. The
> setup below reproduces that machine's layout. A one-click Windows installer is
> in progress (milestone M39); until then, expect some manual setup.

---

## What Sage does

**Voice**
- **"Hey Sage" wake word**: an on-device detector plus a quick local transcript
  check, so a TV or a nearby conversation rarely sets it off.
- **Streaming speech recognition** with Deepgram Flux, with local
  faster-whisper and Parakeet fallbacks.
- **Natural conversation**: interrupt Sage mid-answer, add to your question
  while it is thinking, or just say "stop".
- **Local JARVIS-style voices** (Chatterbox Nano and Turbo, on an NVIDIA GPU),
  with ready-made greetings; a cloud voice (Cartesia) is also supported.
- **Voice fingerprint**: tells your voice from Sage's own, so it does not answer
  itself.

**Help with your day**
- **Mail and calendar**: Gmail and Outlook, summarised and searched.
- **Morning briefing**: messages, calendar, weather, news and notes, written
  ahead of time and read aloud on request.
- **Reminders and scheduled tasks**, including class-schedule notifications,
  delivered by voice, desktop notification or Telegram.
- **Navigation**: routes, places and weather, with a spoken briefing handed off
  to Waze (including from an iPhone Siri Shortcut).
- **Music**: Spotify search and playback control.
- **Web research**: search and multi-step deep research with cited sources.
- **Files and documents**: find files on your PC, attach documents and images to
  a chat, and search your notes vault.
- **Browser and desktop**: drives your Chromium browser for web tasks, and can
  describe what is on screen on request (read-only).
- **Telegram**: talk to Sage from your phone.

**A companion, not just a chat box**
- **Presence and moments**: notices when you sit down or come back, greets you,
  and occasionally starts a conversation, with quiet hours and cooldowns you
  control.
- **Memory**: remembers facts about you, keeps a nightly diary of the day, and
  tidies its memory on its own.
- **Diagrams** drawn on screen when a picture explains better.

**The app**
- A web interface with an animated orb that reacts to Sage's voice, a dedicated
  Voice page, chat history, Data Sources, Memory, Agents, Logs and Settings.
- A **Health** page and `system_health` tool: Sage checks itself on request and
  explains what is degraded and why.

**Safety by default**
- File, Git and command tools only work inside folders you allow, and fail
  closed. There is no unrestricted shell or code execution.
- Keys and tokens stay in your environment or local credential files, never in
  the repository.

## Requirements

- Windows 11
- [uv](https://docs.astral.sh/uv/) (Python 3.11) and Node.js 20+
- [Ollama](https://ollama.com/) for local models (optional if you use cloud models)
- An NVIDIA GPU for the local voices (CUDA 12.8 build); otherwise use the cloud voice
- Accounts and API keys for the services you want: an OpenAI-compatible model
  provider, Deepgram, Tavily, Google (Gmail/Calendar/Maps), Microsoft (Outlook),
  Spotify, Telegram, Cartesia. All are optional; Sage degrades gracefully without
  them and the Health page says what is missing.

## Setup (current, manual)

Sage currently expects this layout:

| Path | Contents |
|---|---|
| `C:\AI\OpenJarvis-Lab` | this repository |
| `C:\AI\OpenJarvis-Data` | Sage's data: `config.toml`, databases, voices, logs (set `OPENJARVIS_HOME` to it) |

1. **Clone** the repository to `C:\AI\OpenJarvis-Lab`.
2. **Python environment.** In the repository:

   ```powershell
   uv sync --extra desktop --extra server --extra speech --extra speech-deepgram `
     --extra inference-cloud --extra inference-google --extra channel-gmail `
     --extra channel-telegram --extra tools-search --extra memory-pdf --group desktop-native
   ```

   > Known gap: `uv.lock` currently trails the environment Sage actually runs
   > (newer `deepgram-sdk` and `anthropic`, among others). Bringing the lock in
   > line is part of the installer work; until then this step may need manual
   > upgrades.

3. **Frontend.** `cd frontend` then `npm install`.
4. **Local voices (optional).** `scripts\setup_voice_sidecar.ps1` builds the
   separate voice environment in `<data folder>\voice-env`.
5. **Configuration.** Create `C:\AI\OpenJarvis-Data\config.toml` (start from
   `jarvis init`), set `OPENJARVIS_HOME`, and put API keys in your user
   environment variables. Allowed folders for file tools go in
   `OPENJARVIS_FILE_READ_DIRS`, `OPENJARVIS_FILE_WRITE_DIRS` and
   `OPENJARVIS_CODING_DIRS`.
6. **Start Sage** with `scripts\start-sage-hidden.vbs` (stop with
   `scripts\stop-sage-hidden.vbs`), then open http://localhost:5173.

The command-line tool is `jarvis` (for example `jarvis ask "..."`, `jarvis doctor`).
Sage-named commands, paths and variables are being added alongside the current
ones without breaking existing setups.

## For developers

- `AGENTS.md`: how to work on this codebase: commands, boundaries, and the traps
  that each cost a real debugging session. Read it before changing anything.
- `ROADMAP.md`: milestones, shipped and planned.
- Tests: `.venv\Scripts\python.exe -m pytest <paths> -p no:randomly -q`;
  frontend: `cd frontend; npx vitest run; npx tsc --noEmit`.
- CI for this branch: `.github/workflows/sage-ci.yml`.

## Acknowledgements

Sage is built on **OpenJarvis**, a framework for on-device personal AI by the
OpenJarvis authors (part of the Intelligence Per Watt initiative at Stanford
SAIL). Much of Sage's server, agent, tool and memory foundation is their work,
used under the Apache License 2.0. See [`NOTICE`](NOTICE) and
[OpenJarvis on GitHub](https://github.com/open-jarvis/OpenJarvis).

## License

Apache License 2.0; see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
