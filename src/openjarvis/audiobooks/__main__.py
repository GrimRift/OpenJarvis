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

from openjarvis.audiobooks import BookStore, import_book
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
    print("queue: PAUSED" if data["paused"] else "queue: running")
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
    can = sub.add_parser("cancel", help="cancel a job (or 'all')")
    can.add_argument("job")
    voi = sub.add_parser("voice", help="set a book's narrator voice")
    voi.add_argument("book")
    voi.add_argument("voice")
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
    elif args.cmd == "cancel":
        print(f"cancelled {queue.cancel(args.job)} job(s)")
    elif args.cmd == "voice":
        book = _find_book(store, args.book)
        store.update(book["id"], voice=check_voice(args.voice))
        print(f"{book['title']}: narrator {args.voice}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
