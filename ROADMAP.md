# Sage roadmap

Milestones for the `feature/sage-customization` fork. Historically these were
tracked in conversation only, which meant a new assistant started blind. Ordered
newest first; anything marked **not started** has been scoped with the user but
not built.

For what has actually shipped, read `HANDOFF.md`. For the traps that cost real
debugging sessions, read `AGENTS.md`.

---

## M35 — Sage in the car (started 2026-09-05; tool slice implemented)

The `navigate` tool now handles saved coordinates, unsaved place searches with
candidate selection, traffic ETA, destination weather and Waze links. Routes,
Places API (New), weather and authenticated drive audio are live-verified.
User confirmed separate 30/day Google quota caps. Implementation and
rollback instructions: `docs/m35-navigation.md`. The authenticated phone endpoint
with Sage audio is built. Shortcut, Tailscale and Waze voice pack remain pending.

**The experience.** In the car: *"Hey Siri, drive home."* Sage checks weather and
traffic, speaks a briefing in its own voice through the phone, then opens
navigation to a saved destination. The user's phrasing for the target feel:
*"Certainly, Sir. Opening Maps and checking the traffic and weather. Drive
safely — I'll handle the rest."*

**Platform facts, verified rather than assumed — do not re-litigate these:**

- **No third party can read live turn-by-turn state out of Google Maps, Waze or
  Apple Maps.** Sage narrating a Maps route is impossible.
- **Google Maps and Apple Maps cannot use a custom voice.** No API, no setting.
- **Waze can, and still does in 2026, including on iPhone** — user-recorded
  custom voices, a fixed list of ~40 prompts. Waze *records through the
  microphone* rather than importing files, so Sage's Cartesia audio has to be
  played into the phone while recording. One-off, and then every turn is Sage.
  This is the only way to get Sage's voice onto turn-by-turn.
- **The browser wake word cannot work while driving on iOS.** Mic capture stops
  when the PWA is backgrounded, and entirely with the screen off. A **Siri
  Shortcut** is the trigger — and it can play audio Sage returns, so the reply
  stays in Sage's voice rather than Siri's.

**Decisions taken:**

- **Traffic/ETA: Google Routes API** on project `708083691179`.
  **Billing correction verified 2026-09-05:** `TRAFFIC_AWARE` requests use Pro,
  not Essentials; do not apply the previously quoted Essentials free allowance.
  New-place lookup also needs Places API (New), Text Search Pro for coordinates
  and names. Each API needs its own enablement and quota review before live use.
  A daily cap limits usage; it does not independently guarantee zero billing.
- **Reachability: Tailscale.** Private network between phone and PC, no open
  ports, works on mobile data. Exposing the server to the internet was
  explicitly rejected: it holds Google tokens and can run code on the machine.
- **SendBlue/iMessage rejected** as the phone path. It would remove the need for
  Tailscale and the PWA, but it is a paid third-party relay and every message
  would cross their servers.
- **Phase 1 is brief-and-hand-off**, with the Waze voice pack built alongside.

**Pieces:** a `navigate` tool on the standard tool pattern (saved place → Google
Routes ETA/traffic → the existing weather connector → briefing text + maps deep
link); saved places in `OPENJARVIS_DATA`, not the repo; `POST /v1/drive`
returning spoken audio plus the maps URL; the Siri Shortcut; Tailscale; the Waze
prompt pack generated from Cartesia.

**Out of scope:** Sage implementing turn-by-turn, and any background wake word on
iOS. Both are platform walls, not effort questions.

**Blocked on the user:** Routes API enabled with a quota cap, optional Places API
for resolving new destinations, Tailscale on both devices, and saved places.
Until lookup is enabled, an unsaved destination returns a Waze search link;
saved or explicitly supplied coordinates produce a navigation link offline.

**Unverified until a real drive:** whether the Shortcut reliably plays returned
audio before opening Waze, and whether the recorded voice pack survives a
speaker-to-microphone round trip.

---

## M34 — Self-diagnostics (scoped 2026-09-02, not started)

A **Health page** plus a `system_health` tool triggered by ordinary phrasing
("check system health"), running diagnostics on demand and answering in chat.
**Pull only, both surfaces** — the user explicitly did not want Sage volunteering
warnings.

**v1 covers** the voice pipeline, models and GPU, and scheduled jobs.
**Credentials and connectors were offered and not chosen**, which is worth
revisiting: that is the area that would have caught most of what went wrong on
2026-09-04.

Build on `speech_health`'s capability-plus-reason shape. `SystemPulse` is
activity rather than health and the Dashboard is cost/energy, so neither is the
home for this.

**The case for it keeps making itself.** Every one of these was found by the user
noticing, never by Sage saying anything:

- the Google OAuth token expired silently and the briefing reported "nothing to
  report" having fetched nothing;
- three Google APIs (Drive, Contacts, Tasks) were disabled at the project level
  and returned 403 for months, diagnosed as a scope problem for just as long;
- the briefing's `world` section was configured on and collected nothing, ever;
- the dashboard read zero for energy, tokens and requests while the machine had
  been answering all day;
- the wake word fired on a muted microphone.

---

## M32 — Desktop UI automation (PARKED 2026-09-02)

Parked, and **do not resume by default.** In the user's words: *"it couldn't
really control Obsidian or Electron apps the way I want it to, it can't even tell
if there's a tab being shown or to click what I want."*

That verdict is accurate and the reason is structural: Electron apps publish
almost no UI Automation tree, so there is no named element to target and no way
to read tab state. The good mechanism (`click_control`, driving an app's own
accessibility API) works only on apps already reachable through files and APIs;
the apps that need automation accept only blind coordinates.

`list_windows`, `inspect_window` and `capture_screen` remain — reading the screen
was never the problem.

---

## Shipped

M0–M31 and M33 are done; `HANDOFF.md` carries the detail, newest first. The most
recent are M31's file-upload half (attach a document, and pages the extractor
mangles get read by looking at them) and the CI lane for this branch.
