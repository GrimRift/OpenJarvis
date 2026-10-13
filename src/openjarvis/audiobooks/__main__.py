"""``python -m openjarvis.audiobooks import <file>`` / ``list``: the store by hand.

Until the Books page exists (M42 step 5) this is how a book gets imported.
"""

from __future__ import annotations

import argparse
import sys

from openjarvis.audiobooks import BookStore, import_book


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m openjarvis.audiobooks")
    sub = parser.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="import a PDF or EPUB into the book store")
    imp.add_argument("path")
    sub.add_parser("list", help="list imported books")
    args = parser.parse_args(argv)

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
        return 0
    for book in BookStore().list():
        print(
            f"{book['id']}: {book['title']} "
            f"({book['chapter_count']} chapters, {book['words']:,} words)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
