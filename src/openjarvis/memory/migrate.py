"""One-time folds into the fact store (M38).

``MEMORY.md`` held hand-curated notes that only the proactive agent ever
read. Its bullet points become pinned curated facts -- one memory, one
page -- and the file is renamed so the fold runs once.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional

from openjarvis.core.config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)

# Sections that are instructions to the assistant, not facts about the user.
_SKIP_SECTIONS = ("memory rules", "rules")


def memory_md_bullets(text: str) -> List[str]:
    bullets: List[str] = []
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            section = stripped.lstrip("#").strip().lower()
            continue
        if section in _SKIP_SECTIONS:
            continue
        if stripped[:2] in ("- ", "* "):
            fact = stripped[2:].strip()
            if fact:
                bullets.append(fact)
    return bullets


def fold_memory_md(store: Any, config_dir: Optional[Path] = None) -> int:
    """Fold MEMORY.md into pinned curated facts. Returns how many were added."""
    from openjarvis.memory.store import TRUST_TRUSTED

    path = (config_dir or DEFAULT_CONFIG_DIR) / "MEMORY.md"
    if not path.exists():
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    added = 0
    for fact in memory_md_bullets(text):
        try:
            if store.add(fact, source="curated", trust=TRUST_TRUSTED, pinned=True):
                added += 1
        except Exception:
            logger.debug("Could not fold %r", fact[:60], exc_info=True)
    try:
        path.rename(path.with_name("MEMORY.md.folded"))
    except OSError:
        logger.warning("MEMORY.md folded but could not be renamed", exc_info=True)
    logger.info("Folded MEMORY.md into %d pinned facts", added)
    return added


def pin_curated(store: Any) -> int:
    """Curated facts written before pins existed are the identity core;
    pin them once so relevance recall never drops them."""
    pinned = 0
    for fact in store.list():
        if fact.source == "curated" and not fact.pinned:
            if store.update(fact.id, pinned=True) is not None:
                pinned += 1
    return pinned


__all__ = ["fold_memory_md", "memory_md_bullets", "pin_curated"]
