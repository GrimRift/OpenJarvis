# M38 — Memory: what Sage keeps, and how it gets it back

Scoped 2026-09-16. Not started.

## What memory is today, measured

Sage has five stores that each hold a different kind of "remembering", and
they are not presented anywhere as one thing:

| Store | What | Where | Reaches the model how |
| --- | --- | --- | --- |
| **Facts** | one-line durable facts, auto-extracted after each exchange by `qwen3.5:4b` (374) plus 14 curated | `memory_facts.jsonl` | injected into every prompt, *newest first until 2,048 tokens* |
| **Profile** | who the user is, hand-written | `USER.md` | in the system prompt |
| **Curated notes** | the `memory_manage` tool's read/add/update/remove | `MEMORY.md` | proactive agent only |
| **Episodes** | one paragraph per day with conversations (M36) | `episodes.json` | last two days injected as "recent days" |
| **Documents** | indexed files and pasted text (the Data Sources → Memory tab) | `memory.db` (sqlite, 1 chunk) | BM25 retrieval, top 5 |

The measurement that matters: **388 facts are about 8,600 tokens; the budget
is 2,048, filled newest-first.** So roughly the newest ninety facts reach
the model and the other three hundred never do -- including the fourteen
curated ones, which are the oldest. Recall is not by relevance at all; it
is by recency. "Sage forgot X" today usually means X is on disk and was
simply crowded out. The `[DUP]`/`[STALE]` review file from an earlier
clean-up shows the second problem: auto-extraction keeps time-relative
lines ("has a class at 11 today") and near-duplicates that then cost budget
every turn.

The Memory tab in the screenshot is the *documents* store only -- a folder
indexer and a paste box -- with no view of the facts, episodes or profile
at all. Nothing in the UI shows what Sage actually remembers about the
user, and nothing lets the user correct it.

## Goals

1. **Recall by relevance, not recency.** What reaches the model for a turn is
   the facts that matter for that turn, plus a small pinned core that always
   goes.
2. **One Memory page.** See, search, add, edit, delete, pin, tag and upload
   -- facts, episodes, documents, profile -- in one place, with provenance.
3. **Hygiene that runs itself.** Duplicates merged, contradictions resolved,
   time-relative facts expired, on a schedule, with a review queue rather
   than silent deletion.
4. **Memory the user can talk to.** "Remember that…", "forget what I said
   about…", "what do you know about…", "why do you think that?" -- by voice
   or text, deterministic tools, not model judgement about whether to store.

## Phase 1 — The Memory page and CRUD (the request in hand)

Replace the Data Sources → Memory tab with a **Memory** page (own sidebar
entry), four sections:

- **Facts.** A searchable, filterable table: text, source (auto / curated /
  you), trust tier, date, and two switches per fact -- *pinned* (always
  in context) and *private* (never used by initiative or in anything Sage
  says first; the M37 "never bring up" list becomes this flag). Add a
  fact; edit in place; delete; multi-select delete. Search is the same
  ranking the model will use, so what you see is what it can recall.
- **Episodes.** The diary, by day: read, edit, delete, "rewrite from the
  day's turns".
- **Documents.** What is there today (index a folder, paste text, search),
  plus **upload** (.md, .txt, .pdf, .docx through the existing document
  readers) and per-document delete.
- **Profile.** `USER.md` editable in place, with the reminder that it is in
  every prompt.

API: `GET/POST/PUT/DELETE /v1/memory/facts`, `PUT /v1/memory/facts/{id}`
flags, `GET/PUT/DELETE /v1/memory/episodes/{day}`, `POST
/v1/memory/documents/upload`, `DELETE /v1/memory/documents/{id}`. Facts
gain an id (a hash of text + created_at; the file format stays JSONL).

Also in phase 1 because it is the actual defect: **relevance recall**.
Context injection selects facts by BM25 score against the user's message
(the backend Sage already has), takes the top N within the budget, and
*always* includes pinned facts. Newest-first stays only as the tie-break.
Curated and profile facts are pinned by default.

## Phase 2 — Hygiene

A nightly job, beside the episode writer, on the cloud model:

- **Dedupe**: near-duplicates merged into the better-worded one, the others
  removed. The `_similar_texts` check that already blocks exact re-adds is
  the seed.
- **Contradictions**: two facts that cannot both be true ("uses Opera" /
  "uses Chrome") -- newest wins, the older is removed, and the decision is
  logged.
- **Expiry**: time-relative facts ("has a class at 11 today", "is
  currently…") expire after a day; the extractor is told not to produce
  them, and the job catches what it still does.
- **Review queue**: every auto-extracted fact is *pending* for 24 hours,
  visible on the Memory page with approve / edit / reject; pending facts
  are still used, but a rejection is one click. The quarantine tier
  (injection-scanner flags) shows in the same queue with its reason.
- **Extraction model**: `gpt-5.6-luna` for extraction, or at least for the
  nightly consolidation; the 4b local model is where the stale and
  duplicate lines come from.

## Phase 3 — Memory you can talk to

- `remember` tool: "remember that my thesis adviser is Dr. Cruz" -- stored
  verbatim as a curated, pinned fact. Deterministic; the model does not
  decide whether it is worth keeping, the user did.
- `forget` tool: "forget what I said about the k-drama" -- searches, shows
  what matched, deletes on confirmation (the same confirm-then-act shape
  as `apply_health_fix`).
- `recall` tool: "what do you know about my capstone?" -- searches facts,
  episodes and documents on demand, beyond what the turn's injection
  carried, and answers with provenance: "from our conversation on 8
  September".
- **Provenance everywhere**: each fact carries the conversation day it was
  extracted from; the Memory page shows it; "why do you think that?" is
  answerable.

## Out of scope

- Changing the document retrieval backend (BM25 → dense embeddings). Worth
  measuring after phase 1; not assumed.
- Memory across machines / the phone (M29).
- Any change to what the extractor is *allowed* to store; the security
  scanner and trust tiers stay as they are.

## Decisions taken (2026-09-16)

- **Recall is relevance plus a pinned core.** BM25 against the user's
  message selects the facts for the turn; curated and profile facts are
  pinned and always included; recency only breaks ties.
- **Auto-extracted facts are used at once and pending for 24 hours**, with
  one-click reject on the Memory page. No approval gate.
- **Extraction on the cloud model (gpt-5.6-luna), switchable back to
  local.** The `[memory] extraction_model` setting stays; the Memory page
  exposes it as Cloud / Local, and the nightly clean-up runs on the cloud
  model regardless.
- **The Memory page is its own sidebar entry.** Data Sources keeps
  connectors and channels.
