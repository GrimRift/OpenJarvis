# M42 — Audiobooks (scoped 2026-10-13; steps 1-5 built 2026-10-13)

Sage turns a book the user owns (PDF or EPUB) into an audiobook in one of Sage's
voices, for personal listening on the PC and the phone. Scoped with the user
2026-10-13; every decision below is theirs. (Moved here from the data folder's
`notes\m42-audiobooks.md` with the step 1 commit; this file is the one to update.)

## Reference book (the test case)

`D:\Downloads\_OceanofPDF.com_The_Artist_Who_Paints_Dungeon_-_Hobby_Writing_Principle.pdf`
(measured 2026-10-13 with pypdfium2 from the OpenJarvis-Lab venv):
- 4,327 pages, ~868,000 words, clean text layer (0 replacement chars, no OCR).
- **372 chapters, and 372 is right** (checked at build, 2026-10-13): 372 bookmarks
  and 372 `CHAPTER N` heading lines, on the same pages, one per page top. The
  numbers run to 375 because the source itself skips 88, 321, 322, 331, 332, 351
  and 352 (the chapters around them are normal length, nothing is merged in) and
  splits 32, 33, 35 and 41 into `.1`/`.2` halves: 375 - 7 + 4 = 372. The EPUB of
  the same book (`...Principle.epub`, same folder) has the identical 372 entries
  and gaps. Headings are only `CHAPTER N`, no title text.
- ~95 h of audio at ~150 wpm; ~2-3 GB MP3 / ~1.4 GB Opus.
- Text uses curly quotes, ellipses and the odd superscript (U+2074): normalise for TTS.

## Decisions (user, 2026-10-13)

- **Engine: local Chatterbox only** (Nano/Turbo, the existing voice sidecar). No cloud TTS.
- **One narrator voice per book.** No dialogue voice, no per-character voices.
- **Voices:** any existing Sage voice (Jarvis, J.A.R.V.I.S., Frieren, Jarvis Turbo...)
  **plus new narrator clones**: the user supplies a clean 10-30 s clip; clone as with
  Jarvis Turbo, offer 2 takes, user picks by ear (see sage_voice_character).
- **Narrator PICKED 2026-10-13: `lotm-narrator` ("LOTM Narrator", Turbo,
  0.6/0.85/300) = the default narrator.** Cloned from narrator A of an
  AI-narrated LOTM audiobook on YouTube (details in HANDOFF M42 item). The user
  must still be able to pick ANY local voice (Nano or Turbo) per book instead:
  the Books page voice picker lists every voice, default lotm-narrator.
  Long sentences must be split (<=160 chars) -- Nano's 30% VRAM cap OOMed on a
  ~230-char sentence; Turbo's cap is 55%.
- **Generation: all three modes, on request:** whole book; a chapter range
  ("chapters 40-60"); rolling ("keep N chapters ahead of where I am"). One queue,
  three ways of filling it.
- **Delivery (all three):**
  1. M4B parts in a folder, e.g. `<Audiobooks>\<Book>\Part 01 (Ch 1-25).m4b`,
     with chapter markers, title, cover (first PDF page/image if any).
  2. Same files also written to a OneDrive folder so they reach the phone.
  3. Telegram on request ("send chapters 5-10 to my phone"): per-chapter MP3/M4A
     via the existing Telegram channel (bot uploads cap at 50 MB per file).
- **Controls (both):** a new **Books page** in Sage (import, voice, per-chapter
  progress, play on PC, pause/resume queue, export) **and chat/voice tools**
  ("make an audiobook of this in the Jarvis voice", "how far is the book?",
  "render chapters 40-60", "send part 2 to my phone").
- **Formats: PDF + EPUB.** No scanned/OCR PDFs in v1.
- **Audiobookshelf: explained, not chosen.** Output files are compatible with it,
  so it can be added later without Sage changes (needs Tailscale off-LAN; iOS app
  situation to re-check then).

## Open questions to settle at build time

- **Speed: MEASURED 2026-10-13 (narrator since picked: lotm-narrator, Turbo -> expect
  the Turbo row below: ~25-28 h GPU for the whole book).**
  Chapter 1 (pages 1-15, 3,190 narrated words; footnotes, translator's note and
  `OceanofPDF.com` dropped), app open, cloud mode (router had no model loaded),
  standalone engine process (live `voice_sidecar.engine` code, copy of the voices)
  so no graph was captured in the live sidecar. Pieces as live: sentences merged
  to <= 90 chars, then `split_for_generation`; 327 pieces, all in the warm
  512/640/768 buckets. Gaps added: 0.08 s piece / 0.3 s sentence / 0.6 s
  paragraph / 0.9 s `"..."` beat / 1.6 s scene break. Levelled to -16 LUFS.

  | Voice | Audio | Wall | RTF | Pace | Peak VRAM (proc / card) | Runaways |
  |---|---|---|---|---|---|---|
  | Jarvis (Nano, `jarvis`) | 18.2 min | 2.5 min | 0.137 | 175 wpm | 1.76 / 4.9 GB | 0 |
  | Jarvis Turbo (`jarvis-turbo`) | 19.3 min | 5.6 min | 0.289 | 165 wpm | 3.58 / 6.8 GB | 21 retries (4 twice), short lines |
  | J.A.R.V.I.S. (Nano, `jarvis-mix-a`) | 18.7 min | 3.0 min | 0.158* | 170 wpm | 1.75 / 5.0 GB | 0 |

  *ran beside a CPU Whisper check, so slightly pessimistic. Card was 3.1 GB
  before each run (dwm + live Nano sidecar + app).
  **Whole book** (868k words at the measured pace = 83-88 h audio; ~95 h at
  150 wpm): Nano Jarvis ~11-13 h, J.A.R.V.I.S. ~13-15 h, Turbo ~25-28 h of GPU
  time. **25-chapter part** (avg chapter ~2,300 words): Nano ~45 min, J.A.R.V.I.S.
  ~55 min, Turbo ~1 h 40 min. Not counting pauses for live speech.
  **Live Sage while rendering** (probe: one 59-char sentence to the live sidecar
  every 20 s, jarvis-mix-a): 0.52-0.56 s idle -> ~0.9 s during Nano, ~1.1 s
  during Turbo (+0.35 / +0.6 s per sentence). No conversation during the runs,
  no new device-side asserts. So the queue must yield to live speech.
  **Long-text faults** (Whisper small.en round-trip per piece, `*.stt.json`):
  most flags are STT misspelling "Seo Gio". Real ones: Turbo REPEATS short lines
  ("It's nice. It's nice.", "Squeak. Squeak.", "It's small, it's small.",
  "Rustle" x2) and voices bare `"...` fragments as "Uh"/"Huh"/"Ah"/"My"; Nano
  sometimes slurs a word ("gentle as silk" -> "gentler silk", "a gourmet and a
  glutton" -> "...in the platen"), hears as fine otherwise. Build fixes: merge
  short lines (< ~14 chars) with a neighbour, never send an ellipsis-only piece
  (make it a pause), STT-check + re-render flagged pieces.
  **VRAM note for the build:** Turbo narrator + live Nano = two models (card
  6.8 GB) or the live sidecar switching models (6-13 s each); a Nano narrator
  shares the live model and only swaps conditioning. The live sidecar's
  `generate()` calls `use_voice` outside its lock, so a book voice and a live
  reply in another voice could cross: fix before reusing the live sidecar.
  **3-min samples, all 6 voices** (same opening, items 0-46, user asked
  2026-10-13): `D:\Downloads\audiobook-test\3-min samples\`. Frieren Nano RTF
  0.13, J.A.R.V.I.S. Turbo 0.24, Frieren Turbo 0.24. Frieren Nano DROPPED two
  lines (came out near-silent: "The well-ripened apple emitted a pleasant
  fragrance.", "And the second rule.") -> the build's STT check must also catch
  pieces whose audio is far too short for their text. Turbo voices add a mumble
  after a bare "Hmm.".
  Files: `D:\Downloads\audiobook-test\Ch01 - *.mp3`; scripts + per-piece logs:
  session scratchpad `m42\` (extract/normalize/bench/finish/stt_check.py),
  not kept.
- **Sharing the GPU with live Sage:** reuse the running voice sidecar (no second
  model copy in VRAM); live speech always wins: rendering pauses between sentences
  while Sage speaks/listens and resumes after. Must not stall replies (sage_latency).
- **Long-form quality:** chunking at sentence/paragraph bounds, consistent loudness
  (-16 LUFS like other clips), pauses between paragraphs/scene breaks, and a
  check for Chatterbox drift/garbles on long runs (spot-listen + maybe STT
  round-trip check per chunk, re-render failures).
- **Rolling mode's "where I am":** Sage only knows playback position for chapters
  played on the PC Books page; for phone listening the user says the chapter.
- **Folder locations:** Audiobooks root and the OneDrive path (OneDrive may redirect
  Documents; ask, as with Pictures\Sage in M40).

## Step 1: import (built 2026-10-13)

`src/openjarvis/audiobooks/`: `pdf.py` and `epub.py` read a book into chapters
of raw paragraphs, `text.py` cleans a paragraph for TTS, `importer.py` puts it
together, `store.py` saves it. No server route or UI yet; by hand:
`python -m openjarvis.audiobooks import <file>` / `list` (with the live venv;
`OPENJARVIS_HOME` picks the data folder). Tests: `tests/audiobooks/` (synthetic
PDF/EPUB builders in `builders.py`; `test_reference_books.py` runs on the three
real books in `D:\Downloads` and skips elsewhere).

**User decisions at build (2026-10-13):** keep chapters as the source has them
(372, `32.1`/`32.2` separate; skipped numbers recorded as `missing_numbers`);
EPUB front/back matter (Cover, Copyright, Table of Contents, Image Gallery,
Characters, Map...) and near-empty entries are imported but flagged `skip`
(the Books page can flip one back); footnotes and translator's notes are KEPT
and read at the end of their chapter.

**Store** (`<data>/books/<id>/`): `book.json` (title, author, language, cover,
source path/format/sha256/pages, `missing_numbers`, `structure` = how chapters
were found, and per chapter: index, label, number, title, words, chars,
paragraphs, notes, skip, skip_reason, source pages/files, file),
`chapters/0001.txt`, `cover.jpg`. Id = title slug + first 8 hex of the file's
SHA-256: re-importing the same file replaces the book (written beside it and
swapped in). The source file is not copied.

**Chapter text format** (what the render step reads): paragraphs separated by a
blank line; `* * *` = scene break; story first, then (if any) `* * *`,
`Notes.` and one paragraph per note. The chapter heading is NOT in the text: it
is `label`/`title` in book.json, for the render step to announce.

**How chapters are found.** PDF: bookmarks and `CHAPTER N` heading lines
(`Chapter 32.1`, `Chapter 7: Title`) reconciled -- a bookmark with a matching
heading on its page starts at the heading; a heading with no bookmark becomes
its own chapter (`structure.heading_only`); a bookmark with no heading starts
at its page top (`bookmark_only`); text before the first chapter is a skipped
"Front matter" entry. EPUB: the EPUB 3 nav, else the NCX, over the spine
(a `#fragment` splits a document; no TOC = one chapter per spine document).

**How paragraphs are found in a PDF.** The text layer has none (the reference
book has no blank lines); they come from line geometry: first-line indent
(margins = outermost edge at least 2% of long lines share, odd/even pages
apart for mirrored print margins -- NOT the commonest edge, which in a
dialogue-heavy book is the indent), a short last line ending in punctuation,
or a gap over 1.6x the line pitch measured from a line's LAST character
(PDFium sometimes reports two visual lines as one). Checked against the same
book's EPUB `<p>` tags: identical paragraph counts in 368 of 372 chapters, the
rest off by one; words within 3 per chapter. Running headers/footers and page
numbers: the topmost/bottommost line (by position) repeating on 30%+ of pages.
Line-end hyphens (PDFium reports them as U+FFFE): dropped only if the book also
spells the joined word whole ("beau-tiful"), else kept ("wood-fired", "Sung-woon").

**Cleaning** (`text.py`): `OceanofPDF.com`; a web-novel watermark planted
mid-sentence a few hundred times, written to dodge search (Cyrillic/Greek look-alike
letters, math-alphabet letters, letters spaced or bullet-separated, wrapped in
`~ ° • ➤ ◆ {}`) plus asides like "(Read more on our source)"; footnote markers
(superscripts incl. U+2074); curly quotes, CJK corner brackets -> straight;
every ellipsis shape -> `...`; symbols, emoji, kaomoji and gibberish words
(non-Latin letters left after folding look-alikes, Latin-script books only);
URLs; letter runs capped at 4 ("Haaaah"), `!!!!` at 2. Notes: paragraphs led
by a superscript or `t/n :`, `TL note:`, `A/N -`, `Note:`...; when they start
in a chapter's last 20 paragraphs (or last seventh), everything after is notes
too (translators chat after their notes, unmarked).

**Reference book result:** 372 chapters, 863,525 narrated words (median
chapter 2,232, min 315, max 5,243), 76 notes in 19 chapters (all within
chapters 1-61), cover = the first page's image, 0 watermark/curly/ellipsis
leftovers; ~12 s to import. LOTM Vol. 1 EPUB: 227 entries, 14 flagged skip,
213 narrated, 419k words.

### For the render step (findings from the 2026-10-13 sample renders)

Not built in step 1; the render step must:
- Split sentences over ~160 chars at a comma (Nano's 30% VRAM cap OOMed on a
  ~230-char sentence; Turbo's cap is 55%).
- Merge pieces under ~14 chars with a neighbour (Turbo repeats short lines:
  "It's nice. It's nice.").
- Never send an ellipsis-only piece (`"..."` paragraphs are kept in the text as
  beats): make it a pause (~0.9 s). Turbo also mumbles after a bare "Hmm.".
- `* * *` -> ~1.6 s pause, paragraph ~0.6 s, sentence ~0.3 s, piece ~0.08 s;
  never voice the marker. Announce the chapter (`label`, plus `title` when it
  differs) before the text; `Notes.` is spoken as written.
- STT round-trip per piece; re-render pieces that come back wrong OR far too
  short for their text (Frieren Nano dropped two lines as near-silence).
- Level to -16 LUFS like the other clips.

## Step 2 result: lotm-narrator benchmark (2026-10-13 ~18:05)

Chapter 1 from the imported store (3,423 words incl. the Notes section), voice
`lotm-narrator` (Turbo 0.6/0.85/300), standalone `ChatterboxEngine` (live
`voice_sidecar.engine` code, scratch copy of the voice), app open, live
sidecar on Nano beside it. Pieces built with the render rules above: chapter
announcement, sentences merged to <= 90 chars, `split_for_generation`, hard cut
at 160, pieces < 14 chars merged, `"..."` lines and `* * *` as pauses (gaps 0.08
piece / 0.3 sentence / 0.6 paragraph / 0.9 beat / 1.6 scene / 1.2 after the
title) -> 321 pieces + 20 pauses, none over 160 chars.

| | lotm-narrator (this run) | Jarvis Turbo (10-13 morning, old piece rules) |
|---|---|---|
| Audio / wall | 22.4 min / 5.15 min | 19.3 min / 5.6 min |
| RTF (wall / audio) | **0.23** (0.26 on speech only) | 0.289 |
| Pace | 153 wpm | 165 wpm |
| Peak VRAM proc / card | 3.69 / 6.23 GB | 3.58 / 6.8 GB |
| Runaway retries | **3** (all recovered) | 21 (4 twice) |

Median 0.9 s generation per piece, max 3.9 s. **Whole book** (863k words at
153 wpm = ~94 h audio): **~22 h of GPU time**; a 25-chapter part (~6.3 h audio)
~1 h 30 min. Model load 12.6 s.

**Whisper round-trip** (small.en, CPU, per piece): 23 of 321 flagged, nearly
all transcription artefacts -- the name "Seo Gio" as "C.O.G.O"/"Jio", the
scorer counting single-quoted lines, "1cm"/"1 cm", "fibre"/"fiber". **No
repeated lines, no too-short pieces** (the < 14-char merge works; Jarvis
Turbo repeated 4 short lines in the morning). Possibly real, to hear:
"gentle as silk" -> "gentler sill" (1:21; Nano slurred the same phrase in the
morning, so maybe the phrase), "Gio thought." -> "Geothart" (7:47). For step 3,
make the STT check name-tolerant (strip quotes; compare names fuzzily) or it
flags every chapter. Output: `D:\Downloads\audiobook-test\Ch01 - LOTM Narrator
(Turbo, imported text).mp3` (-16.8 LUFS, 96 kbps); scripts were scratch only.

**Found for step 3:**
- **Memory, not VRAM, is the limit.** The first attempt died loading Turbo:
  "The paging file is too small" (os error 1455). The live server had grown to
  10.4 GB commit over the day; commit limit = 15.6 GB RAM + a 16 GB pagefile
  that can grow to ~24 GB. After a live restart (server 6.0 GB) it fitted. A
  second engine process (~4-5 GB commit) beside a long-running Sage will not
  always fit, which backs the scope's "reuse the running voice sidecar" -- but a
  Turbo narrator there means Nano<->Turbo switches (6-13 s) or both models
  resident (card 6.2 GB measured with both on it here; commit +~2 GB).
- Importer text nits seen in the pieces: spaces inside quotes from the source
  (`" Squeak ."`), and a one-word heading line merged into the next paragraph
  ("Lulupu A flower..."). Small cleaner fixes.
- Live-Sage latency was not re-measured in this run (10-13 morning: +0.6 s per
  live sentence while Turbo renders).

## Step 3: render queue (built 2026-10-13)

**User decisions at build (2026-10-13 evening):**
- **A separate render process** ("book voice"), not the live sidecar: a second
  `voice_sidecar` on port 8792, started only while the queue has work, stopped
  2 min after it runs dry, below-normal CPU priority. A crash or OOM there
  cannot take Sage's voice down, and live Nano never switches to Turbo. This
  replaces the scope's "reuse the running voice sidecar" (decided before a
  Turbo narrator was picked).
- **M4A, AAC 64 kbps mono** per chapter (~11 MB per 22-min chapter, ~4 GB for
  the reference book): M4B parts can be built by stream copy, Telegram can
  send a chapter as is.
- **Runs whenever Sage is idle**, games and films included (no full-screen
  detection) -- **but gently** (user, 10-13 ~19:20, after the first live
  chapters at full speed "maxed out the system", made things sluggish and
  heated the PC): **speed 2/3** (after a piece that took g s the book voice
  rests g/2 s; user said "2/3 or 75%"; `speed 75` sets the other), a **break
  between chapters** (default 5 min, `break <minutes>`; the book voice is
  UNLOADED for it -- user asked, "if no speed or quality decrease" -- and
  reloaded 45 s before it ends, so the ~35 s load is inside the break and the
  same model/voice comes back), and **Whisper only on pieces under 40 characters**
  (the full check kept 2 CPU threads busy). The user will **raise the
  pagefile maximum** themselves for the memory squeeze (the CPU-threads cap
  offered was not chosen; unloading came with the breaks). At 2/3 + 5 min breaks the
  reference book is ~33 h + ~31 h of breaks of idle time.

**Modules:** `pieces.py` (render rules from step 2), `queue.py` (jobs),
`render.py` (one chapter), `check.py` (listening back), `voiceproc.py` (the
book voice process + memory/VRAM checks), `worker.py` (the thread started by
`jarvis serve`; `SAGE_AUDIOBOOKS=0` turns it off).

**Queue** (`<data>/books/queue.json`, shared by the worker and the CLI under
a lock file): jobs in the order asked; `book` = every non-skip chapter,
`range` = chapters by the book's own numbering ("32" = both halves),
`rolling` = the next N non-skip chapters after `position`, never closes,
refills when the position moves. The worker renders the first chapter of the
first job that has no `<book>/audio/<voice>/NNNN.m4a` yet; a chapter already
made in that voice counts for every job. Audio lives per voice, so changing
the narrator never overwrites anything. Re-importing a book keeps its `audio/`
and its `voice` (new `BookStore.update` changes book.json only).

**By hand, until the Books page:** `python -m openjarvis.audiobooks`
`render <book> [--chapters 40-60] [--voice v]`, `rolling <book> --ahead 5
[--at 12]`, `at <book> <chapter>`, `queue` (jobs + what the worker is doing),
`pause`, `resume`, `speed <percent>`, `break <minutes>`, `cancel <job|all>`,
`voice <book> <voice>`. `<book>` = id,
id prefix or part of the title. The worker's state: `<data>/books/render-status.json`.

**Yielding to live Sage** (before every piece, from `core.activity`, the same
record that keeps reminders off a reply): waits while Sage speaks (server
voice), a TTS stream is open or the page is playing a reply, the mic is
sending a turn to Flux, a reply is pending, for 10 s after the user's turn and
6 s after a reply. A live reply that starts mid-piece waits at most that
piece (median 0.9 s, longest 3.9 s on 10-13); a spoken turn takes longer than
that, so normally the GPU is already free. Pause/cancel take effect at the
next piece.

**Resumable:** every piece is written to `audio/<voice>/work/NNNN/` with a
manifest (digest of the spoken plan + voice) before the next is asked for; a
restart, crash or pause continues from there. A changed text or voice starts
the chapter over.

**Before the book voice starts:** commit headroom (free commit + what the
pagefile may still grow by: 16 GB initial, 24 GB max here) >= 10.5 GB for
Turbo / 8 GB Nano, and free VRAM >= 4.0 / 2.4 GB; otherwise the status says
"not enough memory ... (restarting Sage frees some)" and it retries every
minute. **While rendering**, if less than 1.5 GB can still be committed, the
book voice is stopped at once (pieces kept) so live Sage is never the process
that cannot allocate.

**Measured on live 10-13 19:01 (why those numbers):** the Turbo book voice
held **8.6 GB of commit** while rendering (on WDDM the CUDA allocations count
against commit), the live Nano sidecar 6.1 GB an hour after a start, the
server 4.5 GB; free commit fell to **0.8 GB with the pagefile at its 24 GB
maximum**. The first live run started under the old 7 GB threshold and was
paused by hand. The process that had grown to 10.4 GB by the afternoon was
most likely the live voice sidecar, not the server (step 2 said server). A
larger pagefile maximum would let rendering run beside a long-running Sage;
it is the user's setting to change.

**Listening back** (`check.py`, faster-whisper base.en, CPU, 2 threads, run
while the next pieces render; since 10-13 ~19:20 only pieces under 40
characters, every piece still gets the length checks): a piece is retaken (up to 2 more takes, best
kept) when it is too short (< 45% of the chapter's median s/char), too long
(> 2.6x), a 1-4-word phrase repeats back to back that the text does not
repeat, it has far more words than the text (a long line said twice), or its
character similarity to the text is < 0.55 (garbled). Tuned so names and
quotes do not count: replayed over all 321 step-2 transcripts it flags 0
(the old word-exact score flagged 23). Pieces still failing after their last
take are kept and listed in `status.json`.

**Each chapter:** announcement ("Chapter 1." / "Chapter 7: Crimson."; an EPUB
label that holds the title is said once), pieces through `to_spoken_text`
like every live reply (architecture invariant; mostly dashes -> commas),
gaps, two-pass loudnorm to -16 LUFS (linear), AAC 64k, tags (title, album,
artist, track). Record per chapter in `audio/<voice>/status.json` (seconds,
pieces, generation time, RTF, retakes, flags).

**Cleaner fixes found on the way:** spaces inside quotes (`" Squeak ."`),
plain-digit footnote markers glued to words ("sikhye3", "Speedwagon1": all 21
such words in the book; `to_spoken_text` read them as "a code ending in N G
1").

**End-to-end runs (10-13 ~18:35-18:43, real worker + book voice + Whisper, a
scratch copy of the store, live Sage running):** headroom 9.2 GB, VRAM 4.9 GB
free at start; book voice up in ~35 s. Chapter 258: 32 pieces, 2.2 min audio
in 59 s, 0 flags. **Chapter 1: 321 pieces, 22.5 min audio, RTF 0.238, 388 s
wall incl. start-up (step 2's render alone: 309 s), 2 retakes, 0 flags left;
11.0 MB, -16.4 LUFS.** Whole book: ~22-24 h of idle time.

## Step 4: export -- M4B parts, OneDrive, Telegram (built 2026-10-13 evening)

**User decisions at build (2026-10-13 ~20:30):** parts go to
**`D:\Audiobooks\<Book>\`**, copied to **`OneDrive\Sage\Audiobooks\<Book>\`**;
**25 narrated chapters per part**; a part is written **automatically when all
its chapters are rendered** (and on request).

**Module `audiobooks/export.py`** -- the API the Books page and chat tools call
(all take `store=` / `dest=` for tests; `voice=None` = the book's narrator):
- `export_parts(book_id, voice=None, parts=None, *, partial=False, force=False)
  -> list[Path]`: writes the given parts (default all) that are complete and
  not already up to date; returns the PC files written. `partial=True` also
  writes a part still being narrated: every rendered chapter of it, gaps
  skipped (since 10-14; the chapter markers say which are in), named by the
  first and last ("Part 01 (Ch 1-3).m4b"); the full part replaces it later (an older
  "Part 01 (...)" file is removed in both folders). `export_part(book_id, n,
  ...) -> Path | None` = one part.
- `part_status(book_id, voice=None) -> list[dict]`: per part `part`, `name`,
  `chapters` (indexes), `rendered`/`total`, `complete`, `exported` (the record
  below when the file exists and its chapters have not changed, else None).
- `send_chapters(book_id, voice=None, chapters) -> SendResult`: `chapters` =
  a spec in the book's numbering ("5-10"; "32" = both halves) or a list of
  indexes; one Telegram audio message per chapter (title "Chapter 5",
  performer = book title) via `[notifications] channel`. `SendResult.sent /
  not_made / too_big / failed / error`, `.ok`, `.describe(book)` = a sentence
  for the user. Uploads are slow (chapter 1, 10.9 MB: 55 s), so a chat tool
  should send in the background or say it may take a minute per chapter.
  Telegram can't take a part (~270 MB > 50 MB bot limit): "send part 2" =
  send that part's chapters (`part_status(...)[1]["chapters"]`).
- `auto_export(book_id, voice, index)`: called by the render worker after
  each chapter; writes the chapter's part once it is complete.
  `SAGE_AUDIOBOOKS_AUTO_EXPORT=0` turns that off.
- `plan_parts(book)`, `part_of(book, index)`, `destinations()`.

**Parts:** the book's non-skip chapters in runs of 25, named by the book's
own numbers: `Part 01 (Ch 1-25)`, `Part 02 (Ch 26-46)` (the reference book's
32.1/32.2 halves count as chapters). Built by **stream copy** (ffmpeg concat
demuxer, `-c copy`, ~3 s for 3 chapters) with an ffmetadata file: one chapter
marker per chapter titled like its announcement ("Chapter 7: Crimson"),
times from ffprobe of each chapter file; tags title "<Book> - Part 01 (Ch
1-25)", album = book, artist + album_artist = author, genre Audiobook, track
"1/15", media_type 2 (audiobook), comment = the voice; `cover.jpg` as the
attached picture. Written as `~$<name>.tmp` then renamed (OneDrive skips
`~$` and `.tmp` files, so it never uploads half a part). A voice other than
the book's narrator exports to `<Book> (<voice>)`. Records per voice:
`audio/<voice>/exports.json` (file, PC + OneDrive paths, chapters, partial,
seconds, bytes, size+mtime of every chapter file) -- a part is rewritten only
when a chapter file changed or `force`. A failed OneDrive copy is logged and
the PC file kept. `SAGE_AUDIOBOOKS_DIR` / `SAGE_AUDIOBOOKS_ONEDRIVE` (`off` =
no copy) override the folders; OneDrive = `%OneDrive%`, skipped if missing.

**Telegram:** `TelegramChannel.send_audio(chat, path, caption=, title=,
performer=)` (`sendAudio`, M4A as `audio/mp4`, 300 s timeout). Chapters over
50 MB are reported, not sent.

**CLI:** `parts <book>`, `export <book> [--parts 1,3] [--partial] [--force]
[--voice v]`, `send <book> --chapters 5-10 [--voice v]` (loads
credentials.toml for the bot token like `jarvis serve`).

**Wired into the page and chat (10-14).** User's choices: **Export M4B writes
everything narrated so far** (complete parts + partial ones); **Send to phone
opens a small chapters box prefilled with the next ready chapters from where
you are** (up to 3, e.g. "2-3"), edit and Send.
- **Background send:** `start_send(book_id, voice, chapters) -> status` runs
  `send_chapters` on a thread (one send at a time: RuntimeError while one runs;
  ValueError if none of the chapters is narrated) and keeps
  `<data>/books/send-status.json` (state sending / done / failed / stopped,
  made, not_made, sent, current, estimate_seconds at ~200 KB/s, message).
  `send_status()` reads it ("sending" with no live thread = "stopped": Sage
  restarted mid-send). Status writes retry the swap: on Windows it fails while
  the page's poll has the file open (seen in a test). `delivery(book_id)` =
  folders, part size, auto on/off, `part_status`, this book's last send.
- **Routes:** `GET /v1/books/{id}` now carries `delivery`;
  `POST {id}/export` (`parts`, `partial` default true, `force`) -> written +
  delivery; `POST {id}/send` (`chapters` "5-10" or `indexes`) -> starting
  status at once (400 none narrated, 409 already sending).
- **Page:** the two buttons in the control strip, the send box, and one line
  under it: parts exported (partial count) to the PC folder + OneDrive, or
  that complete parts export by themselves; "Sending to phone: Chapter 2 (1 of
  3)" with a spinner, then the result sentence.
- **Chat tools** (live config `[agent] tools`): `audiobook_export` (writes what
  is narrated, says which parts and where) and `audiobook_send` (`chapters`, or
  `part` = that part's chapters; starts the background send and says how many
  and about how long; lists chapters not narrated yet). Data-boundary audit:
  export = local access (OneDrive syncs, the tool sends nothing),
  send = channel outbound like `image_to_phone`.

**Verified 10-13 ~20:45 on the reference book (chapters 1-3, live data,
staging code):** `export --parts 1 --partial` -> `D:\Audiobooks\The Artist Who
Paints Dungeon\Part 01 (Ch 1-3).m4b`, 32.5 MB in 3 s; ffprobe: AAC 24 kHz,
3 chapter markers at 0 / 1348.6 / 2591.7 s ending 4016.9 s, duration 4016.94 s
= sum of the chapter files (1348.6 + 1243.1 + 1425.2), all tags, cover as
attached_pic; OneDrive copy identical. `send --chapters 1`: sent in 55 s; the user
confirmed it arrived on the phone and plays.
Tests: `tests/audiobooks/test_export.py` (real ffmpeg on tiny sine chapters,
fake phone; the worker's auto-export), `tests/audiobooks/conftest.py` points
every audiobook test's export folders at tmp.

## Step 5: Books page + chat tools (built 2026-10-13 evening)

**User decisions:** layout "M1" -- a mix of mockups B (list + detail) and C
(now listening), picked from `C:\AI\Sage-Staging-Tools\mockups\m42-books\`
(`books-page-variants.html`, `books-page-mix.html`): book list left; the open
book starts with a big now-listening player (cover, chapter, scrubber,
prev/-15 s/play/+15 s/next), then the worker status, one control strip
(narrator, render mode: keep N ahead / chapters / whole book, Queue, Pause
all, speed, break; Export M4B / Send to phone greyed until step 4), job chips
with cancel, the chapter table (play, read, #, title, length or live %,
state); a mini player sticks to the bottom once the big one scrolls away.
Playing **follows "where you are" automatically** (saved every 15 s and on
pause; rolling jobs render ahead of it; at the end of a chapter the next
made one plays, else the position parks on the next chapter). **Read a
chapter yourself** (user asked mid-build): every chapter, made or not, opens
in a reader (same cleaned text, scene breaks drawn, Notes heading,
prev/next, text size kept per viewer); reading does not move "where you
are". Chat tools: render / pause / status, play on the PC, import a file,
export/send once step 4 lands.

**Backend:** `audiobooks/library.py` = the one layer the page and the tools
share (find a book by title piece, summaries, detail with per-chapter state
ready/rendering/queued/skipped/not made from files + queue + worker status,
render, set voice, set position, chapter text, spoken `describe`).
`server/book_routes.py` (`/v1/books`, API-key guarded): list, `import`
(multipart PDF/EPUB, kept in `<data>ooks\_sources\`), `queue` GET/PUT
(paused, speed, break_minutes), `queue/jobs/{id}` DELETE, `{id}`,
`{id}/cover`, `{id}/chapters/{n}/text`, `{id}/chapters/{n}/audio`
(FileResponse, Range for seeking), `{id}/voice`, `{id}/render` (chapters /
ahead / whole), `{id}/position`. Re-import keeps `voice` and the position.

**Chat tools** (`tools/audiobook_tool.py`, local-access in the data-boundary
audit; enabled by adding them to `[agent] tools` in the live config.toml):
`audiobook_status`, `audiobook_render` (chapters / ahead / voice by name or
label), `audiobook_pause` (pause / resume / cancel RENDERING),
`audiobook_import` (PDF/EPUB from OPENJARVIS_FILE_READ_DIRS, Downloads,
Documents, Desktop, OneDrive, D:\Downloads only), `audiobook_play`
(play / pause / resume LISTENING; chapter number, 'continue', 'next'):
returns `metadata.audiobook_play`, which InputArea hands to the book player
on tool_call_end (the route pictures and the weather panel take, so it
works from the Voice page too).

**Frontend:** `pages/BooksPage.tsx`, `components/Books/BookPlayerHost.tsx`
(the one `<audio>`, mounted in App so a chapter keeps playing across pages;
audio and covers fetched with the key as blob URLs; volume =
`volumeFor('chat')`, Sage's voice volume -- architecture invariant), 
`components/Books/ChapterReader.tsx`, `lib/books.ts`, `lib/book-player.ts`
(store + pure decisions, tested). **The player steps aside** while Sage
listens, writes or speaks (voiceState / reply audio / server voice /
streaming) and comes back 1.5 s after, unless the user paused it.

## Suggested build order

1. ~~Import~~ DONE 2026-10-13 (above).
2. ~~Benchmark~~ DONE 2026-10-13 ~18:05 (user chose a quick lotm-narrator run
   over the 2-voice pick): see "Step 2 result" above.
3. ~~Render queue~~ DONE 2026-10-13 (above).
4. ~~Export~~ DONE 2026-10-13 (above): M4B parts + OneDrive copy + Telegram
   send; Books page Export / Send buttons + `audiobook_export` /
   `audiobook_send` chat tools wired 2026-10-14.
5. ~~Books page + chat tools~~ DONE 2026-10-13 (above).
6. Narrator cloning flow for new voices.

## Step 7 (SCOPED 2026-10-14, not built): Audiobookshelf with progress sync

Goal: the narrated chapters show up in Audiobookshelf (ABS) on the phone as they
are made, and listening position syncs both ways with Sage's "where you are", so
rolling mode follows phone listening too (today Sage only knows PC listening).

- **Server on this PC.** Docker Desktop + WSL2 is the usual route but costs RAM this
  machine does not have (see the 10-06 memory crunch); a community native Windows
  build exists (mikiher/audiobookshelf-windows) -- check it is current. Off the home
  network it needs Tailscale on PC + phone.
- **Library.** Sage writes each chapter (already -16 LUFS AAC .m4a) into an ABS library
  folder, e.g. `D:\Audiobooks\ABS\<Book>\NNNN - Chapter N.m4a` (copy or hardlink of
  `audio\<voice>\NNNN.m4a`), track number + title tags set; ABS's folder watcher (or
  `POST` scan) adds new tracks to the same book item. A Redo replaces the file ->
  rescan. M4B parts stay as they are (25 chapters is too coarse for a growing book).
- **Progress sync.** API token (Bearer). Read the user's progress for the item
  (`/api/me/progress/<itemId>`), turn `currentTime` into a chapter via the tracks'
  `startOffset`s, and move Sage's position (+ rolling job) when ABS is newer; the PC
  player's saves go the other way (PATCH progress). Newest `lastUpdate` wins.
  The public API docs say they are out of date: verify paths, and that `currentTime`
  is cumulative over tracks, against a real server first.
- **Phone app.** The official ABS iOS app is still TestFlight-only (10-2026). App
  Store clients: SoundLeaf (free, offline), Absorb, Still; ShelfPlayer's repo is
  archived. User picks by trying one.
- **Telegram auto-send** (5bd22a99) stays as the fallback; probably switched off once
  ABS works.
- **Open questions for the user:** native vs Docker; which phone app; Tailscale OK;
  per-chapter tracks (recommended) vs parts; does ABS replace Telegram.
- **Size:** ~1 session for install + library writer, ~1 for progress sync + tests,
  then a real phone check.
