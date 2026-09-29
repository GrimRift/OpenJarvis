# M40 — Image generation (scoped 2026-09-29, not started)

Sage creates and edits images on request, in chat and by voice. Scoped with the
user 2026-09-29; every decision below is theirs. Build in a fresh session
(prompt: `next-session-m40.md`). Move this file into the repo as
`docs/m40-image-generation.md` in the first commit.

## Decisions (user, 2026-09-29)

- **Engine: cloud OpenAI only**, with the existing `OPENAI_API_KEY`. No local model
  (RTX 5050 8 GB is already ~7/8 GB during voice), no toggle.
- **Model: chosen by benchmark first.** The key lists `gpt-image-2`,
  `gpt-image-2.5-flare`, `gpt-image-2.5-sunburst`, `gpt-image-1.5`, `gpt-image-1`,
  `gpt-image-1-mini`, `chatgpt-image-latest` (read from `models.list()`
  2026-09-29). User approved spending ~6-10 images: the same 2 prompts on
  gpt-image-2, 2.5-flare, 2.5-sunburst; record time, cost, and show the results
  side by side (save them, open them for the user) so the USER picks the default.
  Include one edit call in the benchmark. Price per image: take it from the API's
  returned usage + OpenAI's published pricing; if a variant's price can't be
  found, say so rather than guess.
- **Abilities (all three):**
  1. Create from a description.
  2. Edit an attached/pasted image ("make the sky sunset", "remove the background").
  3. Refine the last generated image ("brighter", "now anime style").
- **Where results go (all three):**
  1. Shown in chat, persisted across reload, click for full size.
  2. Saved to `C:\Users\yanso\Pictures\Sage\`, named `YYYY-MM-DD_short-slug.png`
     (no overwrite; add a suffix on collision).
  3. Sent to Telegram when asked ("send it to my phone") via the existing channel.
- **Cost: no limit, but visible.** Each image's cost shows in the message footer
  and is counted on the Dashboard/savings (write it into telemetry.db `cost_usd`
  so existing aggregation picks it up; check what the Dashboard reads).
- **Presentation = like diagrams, with its own Settings.** Mirror the diagram
  feature (`lib/diagram-presenter.ts`, `components/Diagram/*`, Settings >
  "Diagrams"): a card in the message, and a new image auto-opens once in an
  overlay over the app (Esc/Close dismisses; history never reopens it; works on
  the Voice page too). A new Settings > "Images" section holds its options at
  least: on/off, default model, quality/size, open-automatically, save folder.
  By voice: Sage says a short line ("Here's your cafe, Sir.") and the image
  opens in that overlay.

## Benchmark result + choice (2026-09-29)

Files: `notes\m40-benchmark\` (comparison.png, comparison2.png, results*.json).
All at 1024x1024, quality=medium. Published prices (same for all three):
text in $5/M, image in $8/M, image out $30/M. Cost = returned usage x price.

| model | create | edit | out tokens |
|---|---|---|---|
| gpt-image-2 | 37-51 s, $0.053 | 43-52 s, $0.061 | 1756 |
| gpt-image-2.5-flare | 12-34 s, $0.013 | 14-17 s, $0.021 | 439 |
| gpt-image-2.5-sunburst | 16-19 s, $0.013 | (not measured) | 439 |

Edits accept `background="transparent"` (real RGBA back). 12 calls, $0.39 total.
**User chose: default model `gpt-image-2.5-flare`, quality `medium`.**

## Design (agreed with the user 2026-09-29)

- Tools: `image_generate(prompt)` (replaces DALL-E tool), `image_edit(instruction,
  image="attached"|"last"|<id>)` (edit pasted + refine), `image_to_phone(image)`
  (Telegram sendPhoto via `[notifications] channel`). Result text = one line with
  the image id + cost; never image data. Picture reaches the UI via
  `tool_call_end` metadata `image`.
- Pasted image: per-turn ContextVar holds the newest user message's images.
  **User chose: edit wording routes an image turn to the tool loop**; other
  image turns stay on the vision path.
- Refine: model passes the id from history; `last` = newest in the index.
- Storage: `Pictures\Sage\YYYY-MM-DD_slug.png` (suffix on clash) + index
  `OpenJarvis-Data\images\index.db` (id, path, prompt, model, cost, **parent id
  only -- user chose no before/after UI**). `GET /v1/images/{id}` serves it.
- Cost: one telemetry.db row per image; unknown price = "unknown", not 0. Footer
  shows image cost. **Dashboard: new tile, all cloud spend + images broken out
  (user chose).**
- Settings > Images server-side (`OpenJarvis-Data\image_settings.json`), except
  open-automatically (client).

## What exists (verified 2026-09-29)

- `src/openjarvis/tools/image_tool.py` (`image_generate`): upstream, DALL-E 3,
  returns an expiring URL, sizes 256/512/1024, NOT in `config.toml [agent] tools`.
  Replace rather than extend.
- Pasted images are ephemeral: only the current turn's images are sent
  (`InputArea.tsx` "Only this turn's images"). The edit tool needs the turn's
  attached image bytes on the server side -- design how the tool references
  "the attached image" without the model passing base64 through arguments.
- Refine needs the previous generated image by id (file on disk + message
  metadata), so "make it brighter" works after a reload too.
- Tool results and sources flow to the UI via `tool_call_end` metadata in the
  streaming loop (`server/routes.py`); the replay of earlier tool results is
  cut to 500 chars + sources (`lib/link-preview.ts replayedToolResult`), so
  image data must never be inlined in tool result text.

## Pitfalls to design for

- Generation takes ~10-30 s+: stream a "drawing..." state; the 15-round turn
  loop and voice turn must not time out or double-trigger.
- Content refusals from OpenAI: report honestly, no retry loop.
- New tool must be added to `config.toml [agent] tools` in live data (back up
  config first) and to any `serve.py`/SystemBuilder wiring (known trap: serve.py
  misses what SystemBuilder wires).
- The Tauri app: overlay must work with the window hidden/shown
  (`__sageWindowHidden`), and full-size open should not rely on `window.open`
  of a data: URL (only http/https go to the browser; serve the file over the
  API instead).
- Privacy: pasted photos leave the machine for edits (cloud-only by decision).

## Done when

Create, edit and refine each verified live in the app (chat and voice), file in
Pictures\Sage, Telegram delivery tested, cost visible in footer and Dashboard,
Settings > Images works, tests for the tool/storage/cost path, CI green.
