"""``python -m openjarvis.audiobooks <command>``: the book store and render
queue by hand, until the Books page and chat tools exist (M42 step 5).

The running server's worker does the rendering; these commands only change
the queue (``<data>/books/queue.json``) and read its status.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from openjarvis.audiobooks import BookStore, export, import_book
from openjarvis.audiobooks.queue import RenderQueue, check_voice, parse_chapters


def _find_book(store: BookStore, ref: str) -> Dict[str, Any]:
    """A book by id, id prefix or a piece of its title."""
    books = store.list()
    exact = [b for b in books if b["id"] == ref]
    hits = exact or [
        b for b in books if b["id"].startswith(ref) or ref.lower() in b["title"].lower()
    ]
    if len(hits) != 1:
        names = ", ".join(b["id"] for b in hits) or "none"
        raise SystemExit(f"{ref!r} matches {len(hits)} books ({names})")
    return store.load(hits[0]["id"])


def _print_queue(store: BookStore, queue: RenderQueue) -> None:
    data = queue.load()
    state = "PAUSED" if data["paused"] else "running"
    print(
        f"queue: {state}, speed {round(data['speed'] * 100)}%, "
        f"{data['break_minutes']:g} min break between chapters"
    )
    status_path = store.root / "render-status.json"
    if status_path.is_file():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        line = f"worker: {status['state']}"
        if status.get("chapter"):
            line += f", chapter {status['chapter']}"
        if status.get("pieces"):
            line += f" piece {status.get('piece', 0)}/{status['pieces']}"
        if status.get("reason"):
            line += f" -- {status['reason']}"
        print(f"{line} (at {status['updated']})")
    else:
        print("worker: no status yet (is Sage running?)")
    for job in data["jobs"]:
        if job["state"] != "active":
            continue
        done, total = queue.progress(job)
        what = (
            f"rolling {job['ahead']} after chapter #{job['position']}"
            if job["mode"] == "rolling"
            else f"{job['mode']} ({total} chapters)"
        )
        print(
            f"  {job['id']}  {job['book']}  {job['voice']}  {what}: {done}/{total} done"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m openjarvis.audiobooks")
    sub = parser.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="import a PDF or EPUB into the book store")
    imp.add_argument("path")
    sub.add_parser("list", help="list imported books")
    ren = sub.add_parser("render", help="queue a whole book or some chapters")
    ren.add_argument("book", help="book id, id prefix or part of the title")
    ren.add_argument("--chapters", help='e.g. "40-60" or "1-3,9" (book numbering)')
    ren.add_argument("--voice", help="a local Sage voice (default: the book's)")
    rol = sub.add_parser("rolling", help="keep N chapters rendered ahead of you")
    rol.add_argument("book")
    rol.add_argument("--ahead", type=int, default=5)
    rol.add_argument("--at", help="the chapter you are on (book numbering)")
    rol.add_argument("--voice")
    at = sub.add_parser("at", help="say which chapter you are on (rolling jobs)")
    at.add_argument("book")
    at.add_argument("chapter")
    sub.add_parser("queue", help="show the queue and what the worker is doing")
    sub.add_parser("pause", help="pause rendering")
    sub.add_parser("resume", help="resume rendering")
    brk = sub.add_parser("break", help="minutes to rest after each chapter (0 = none)")
    brk.add_argument("minutes", type=float)
    spd = sub.add_parser("speed", help="how hard to work: 100 = flat out, 67 = 2/3")
    spd.add_argument("percent", type=float)
    can = sub.add_parser("cancel", help="cancel a job (or 'all')")
    can.add_argument("job")
    voi = sub.add_parser("voice", help="set a book's narrator voice")
    voi.add_argument("book")
    voi.add_argument("voice")
    prt = sub.add_parser("parts", help="the book's M4B parts and what is exported")
    prt.add_argument("book")
    prt.add_argument("--voice")
    exp = sub.add_parser("export", help="write M4B parts (PC folder + OneDrive)")
    exp.add_argument("book")
    exp.add_argument("--voice")
    exp.add_argument("--parts", help='e.g. "1" or "1,3" (default: every part)')
    exp.add_argument(
        "--partial", action="store_true", help="also parts not fully narrated yet"
    )
    exp.add_argument("--force", action="store_true", help="rewrite up-to-date parts")
    snd = sub.add_parser("send", help="send chapters to the phone (Telegram)")
    snd.add_argument("book")
    snd.add_argument("--chapters", required=True, help='e.g. "5-10" (book numbering)')
    snd.add_argument("--voice")
    args = parser.parse_args(argv)

    store = BookStore()
    queue = RenderQueue(store)

    if args.cmd == "import":
        book = import_book(args.path)
        skipped = [c["label"] for c in book["chapters"] if c["skip"]]
        print(f"{book['id']}: {book['title']} -- {book['author']}")
        print(
            f"  {book['chapter_count']} chapters "
            f"({book['narrated_chapters']} narrated), "
            f"{book['words']:,} words, cover: {book['cover'] or 'none'}"
        )
        if book["missing_numbers"]:
            print(f"  numbers missing from the source: {book['missing_numbers']}")
        if skipped:
            print(f"  skipped: {', '.join(skipped)}")
        print(f"  structure: {book['structure']}")
    elif args.cmd == "list":
        for book in store.list():
            print(
                f"{book['id']}: {book['title']} "
                f"({book['chapter_count']} chapters, {book['words']:,} words, "
                f"voice {book.get('voice') or 'default'})"
            )
    elif args.cmd == "render":
        book = _find_book(store, args.book)
        if args.chapters:
            chapters = parse_chapters(book, args.chapters)
            job = queue.add(book["id"], "range", chapters=chapters, voice=args.voice)
        else:
            job = queue.add(book["id"], "book", voice=args.voice)
        print(
            f"queued {job['id']}: {len(job['chapters'])} chapters of "
            f"{book['title']} in {job['voice']}"
        )
    elif args.cmd == "rolling":
        book = _find_book(store, args.book)
        position = parse_chapters(book, args.at)[0] if args.at else 0
        job = queue.add(
            book["id"], "rolling", ahead=args.ahead, position=position, voice=args.voice
        )
        print(
            f"queued {job['id']}: {job['ahead']} chapters ahead of "
            f"#{position} in {job['voice']}"
        )
    elif args.cmd == "at":
        book = _find_book(store, args.book)
        index = parse_chapters(book, args.chapter)[0]
        print(
            f"{queue.set_position(book['id'], index)} rolling job(s) now after #{index}"
        )
    elif args.cmd == "queue":
        _print_queue(store, queue)
    elif args.cmd in ("pause", "resume"):
        queue.set_paused(args.cmd == "pause")
        print(f"queue {args.cmd}d")
    elif args.cmd == "speed":
        speed = queue.set_speed(args.percent / 100)
        print(f"speed {round(speed * 100)}%: rests {1 / speed - 1:.2f} s per s of work")
    elif args.cmd == "break":
        print(f"{queue.set_break(args.minutes):g} min break between chapters")
    elif args.cmd == "cancel":
        print(f"cancelled {queue.cancel(args.job)} job(s)")
    elif args.cmd == "voice":
        book = _find_book(store, args.book)
        store.update(book["id"], voice=check_voice(args.voice))
        print(f"{book['title']}: narrator {args.voice}")
    elif args.cmd == "parts":
        book = _find_book(store, args.book)
        dest = export.destinations()
        print(f"PC: {dest.root}  OneDrive: {dest.onedrive or 'off'}")
        for part in export.part_status(book["id"], args.voice, store=store):
            done = part["exported"]
            where = (
                f"exported {done['file']}" + (" (partial)" if done["partial"] else "")
                if done
                else "not exported"
            )
            made = f"{part['rendered']}/{part['total']}"
            print(f"  {part['name']}: {made} narrated, {where}")
    elif args.cmd == "export":
        book = _find_book(store, args.book)
        numbers = [int(n) for n in args.parts.split(",")] if args.parts else None
        written = export.export_parts(
            book["id"],
            args.voice,
            numbers,
            partial=args.partial,
            force=args.force,
            store=store,
        )
        for path in written:
            print(f"wrote {path}")
        if not written:
            print("nothing to write (parts not complete, or already up to date)")
    elif args.cmd == "send":
        from openjarvis.core.credentials import inject_credentials

        inject_credentials()  # the bot token, as `jarvis serve` loads it
        book = _find_book(store, args.book)
        result = export.send_chapters(
            book["id"], args.voice, args.chapters, store=store
        )
        print(result.describe(book))
        return 0 if result.ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
