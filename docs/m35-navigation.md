# M35 navigation — first slice

Implemented 2026-09-05: registered `navigate` tool and authenticated phone
endpoint. They return a briefing and Waze URL; neither launches an app or
claims navigation has started. Provider paths default off in source, but the
user has now enabled them. Routes, Places (New), destination weather and Sage
MP3 were verified together through the authenticated endpoint in 5.2 seconds.

## Current behavior

- A saved name or supplied coordinates produces a Waze navigation link.
- An unsaved name/address uses Places Text Search only when enabled. Multiple
  results return candidates; repeat the same query with the user-selected
  `place_id`. A missing/invalid choice never silently picks the first result.
- Without Places setup, it returns a Waze **search** link. This is not a
  resolved destination and has no ETA.
- Routes uses supplied origin and destination coordinates with `TRAFFIC_AWARE`.
  Missing origin asks for the user's location. It never uses the PC's GPS for
  a phone request. Missing duration is unavailable, not zero; missing static
  duration leaves traffic delay unknown. Waze may choose a different route.
- Destination weather uses the existing OpenWeatherMap functions and summary.
- Google requests have fixed endpoints, narrow field masks, a 10-second timeout,
  no redirects and no retries. Failures never quote provider bodies or keys.
- The read-only tool does not create or edit saved places.

## Setup when live use is approved

Config: `C:\AI\OpenJarvis-Data\config.toml`, never the repository.
`navigate` is appended to `[agent].tools`. Provider configuration defaults to:

```toml
[navigation]
routes_enabled = false
routes_quota_confirmed = false
places_enabled = false
places_quota_confirmed = false
weather_enabled = false
```

The user enables the chosen Google APIs, reviews billing and sets quota caps,
then sets `GOOGLE_MAPS_API_KEY` privately in Sage's environment. No keys in
tool arguments, examples, docs or Git. Each provider requires both its enabled
flag and quota-confirmed flag to be boolean true. Weather has its own opt-in
and uses the existing connector configuration. Restart Sage after config edits.

Saved places: `C:\AI\OpenJarvis-Data\saved_places.json`, a JSON object keyed
by name with `latitude` and `longitude` numeric fields. Names are matched
case-insensitively. Do not store real home/work coordinates in this repository.

## Billing correction and API references

Traffic-aware Routes uses **Pro**, not the roadmap's earlier Essentials label.
Places search with names and coordinates uses **Text Search Pro**. The earlier
10,000-call Essentials figure is not the allowance for this implementation.
A daily quota bounds usage; confirm the relevant SKU allowance separately.

- [Routes request contract](https://developers.google.com/maps/documentation/routes/compute_route_directions)
- [Routes billing](https://developers.google.com/maps/documentation/routes/usage-and-billing)
- [Places search and field SKUs](https://developers.google.com/maps/documentation/places/web-service/text-search)
- [Waze deep links](https://developers.google.com/waze/deeplinks)

## Rollback

Local annotated tag `rollback/pre-m35-2026-09-05` points to
`19c2f2e86976e3866028a9f8f6ba2c27579a959a`. Starting tree was clean, including
untracked-file inspection. CI passed on `fdb35337`; the only changes from that
commit to the tag were AGENTS.md, HANDOFF.md and ROADMAP.md.

Config backup: `C:\AI\OpenJarvis-Data\config.toml.pre-m35-20260905`.
To roll back, first preserve any subsequent work, restore code from the tag
and the config backup, and restart with the existing Sage Start menu shortcut.
Git does not restore external configuration or Python packages. No M35 commit
or push has been made. Never use `git add -A` for this repository.

## Verification and remaining work

Tests use temporary directories and block unexpected network calls. Fresh
process discovery was confirmed failing on the stashed pre-M35 source, then
passing after restoration. The server's tool builder and actual ToolExecutor
dispatch are covered, not just a direct helper invocation.

Web impact: shared server-side tool; existing chat tool/result/link contracts
are used without frontend changes. No frontend build is needed for this slice.

`POST /v1/drive` is implemented with required Sage bearer authentication,
required phone origin coordinates, optional Jarvis/Frieren MP3 audio encoded
as base64, and `Cache-Control: no-store`. Navigation and synthesis run off the
event loop. Audio failure preserves the Waze link; ambiguous destinations
return candidates without synthesis. No persistent audio files are created.

Phone request fields: `destination`, `origin: {latitude, longitude}`, optional
`destination_coordinates`, `place_id`, `voice` (jarvis/frieren), `include_audio`
(default true). Response includes `status`, `maps_url`, `message`, and `audio`:
`{base64, mime_type, voice}` when available. Check `audio_status` before decoding.
Only open navigation automatically for `status=ready`. For `needs_selection`,
show candidates and repeat the original destination query with the selected ID;
with no candidates, show the message instead of assuming a destination.

Siri Shortcut sequence: Get Current Location → ask/dictate destination → POST
JSON with `Authorization: Bearer <Sage server key>` → handle candidate choice →
Base64 Decode audio → Play Sound → Open URL (`maps_url`). Use the private
Tailscale address. The Sage server key is separate from the Google Maps key;
never place the Google key on the phone. Actual Shortcut creation and ordering
still need device validation.

Live setup update: user confirmed separate 30/day caps for Routes and Places.
The initial Places `SERVICE_DISABLED` response was resolved by enabling **Places
API (New)**. Both Google APIs use the user's `GOOGLE_MAPS_API_KEY`. The separate
`OPENJARVIS_API_KEY` user variable now protects all Sage APIs and WebSockets;
unauthenticated drive requests return 401. Browser Settings → Connection → API
key must contain that same Sage secret, not the Google key. Do not change the
loopback bind until private Tailscale reachability is configured.

Enabling authentication exposed the TTS stream's missing auth subprotocols.
Fixed only that transport and configured-base handling, with 42 focused
frontend tests and a rebuilt bundle. Existing fallback behavior is unchanged
by explicit user decision. The fallback still attaches a replay player whose
plain protected audio URL returns 401; this remains a known edge case, not
permission to redesign playback. Actual voice/rearm verification is pending.

Remaining M35: phone authentication/setup, Siri Shortcut, Tailscale, Waze voice
pack and a real drive. Do not expose the existing server publicly or enable
unrelated Google APIs as part of this work.

Environment lesson: plain `uv sync` prunes optional runtime packages. Use
`uv run --frozen --no-sync` for checks in the configured Sage environment;
sync only with the correct extras/inexact mode. During this session the removed
packages were restored at their prior versions before runtime verification.
