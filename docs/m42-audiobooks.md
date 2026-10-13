# M42 — Audiobooks (scoped 2026-10-13; steps 1-3 built 2026-10-13: import, benchmark, render queue)

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

## Suggested build order

1. ~~Import~~ DONE 2026-10-13 (above).
2. ~~Benchmark~~ DONE 2026-10-13 ~18:05 (user chose a quick lotm-narrator run
   over the 2-voice pick): see "Step 2 result" above.
3. ~~Render queue~~ DONE 2026-10-13 (above).
4. Export: M4B parts with chapters + OneDrive copy; Telegram send.
5. Books page + chat tools.
6. Narrator cloning flow for new voices.
