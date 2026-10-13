"""M42 audiobooks: a book the user owns (PDF or EPUB) read aloud in a Sage voice.

Step 1 (this package so far) imports a book into chapters of cleaned,
speakable text under ``<data>/books``. Rendering, export and the Books page
come later; the design and every decision behind it is in
``docs/m42-audiobooks.md``.
"""

from openjarvis.audiobooks.importer import import_book
from openjarvis.audiobooks.store import BookStore

__all__ = ["BookStore", "import_book"]
