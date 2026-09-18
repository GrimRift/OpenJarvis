"""The words Deepgram should expect to hear.

Flux is a general English model that has never met this user's vocabulary.
Unboosted it wrote "ACGE" and "addition" for *Hey Sage*, "Close to the
diagram" for *close the diagram*, and "in case on those" for *Quezon
Province*. Term boosting is the one lever that fixes the first two: a word
the model is told to expect comes back spelt right.

Three sources, merged:

* ``BUILT_IN`` -- the assistant's own name, the phrases Sage acts on, the
  places and apps that come up here, and a few Tagalog words the user
  actually says.
* the class schedule -- instructors and subject names, read from the local
  file, so "Revilloza" is not a lottery.
* the user's own list, edited in Settings and kept in ``keyterms.json``.

Boosting is not free: every term makes the recogniser likelier to hear that
word where it isn't, so the list is capped and short ambiguous words are
left out deliberately.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import List, Optional

from openjarvis.core.config import DEFAULT_CONFIG_DIR

logger = logging.getLogger(__name__)

#: Deepgram accepts repeated ``keyterm`` params; this is a self-imposed
#: ceiling. Past it the URL grows and every extra word is another chance to
#: mishear something as that word.
MAX_TERMS = 60

#: Terms shorter than this are left out: a boosted two-letter word turns up
#: everywhere. Tagalog "po" and "at" are exactly the trap.
MIN_LENGTH = 4

BUILT_IN: tuple[str, ...] = (
    # The name, and the phrase that opens every turn.
    "Sage",
    "Hey Sage",
    # What Sage is asked to do, in the words that were misheard.
    "diagram",
    "illustration",
    "close the diagram",
    # Where the user lives and studies.
    "Quezon",
    "Quezon Province",
    "Laguna",
    "Calamba",
    "Manila",
    "National University",
    # The things on this machine.
    "OpenJarvis",
    "Obsidian",
    "Spotify",
    "Netflix",
    "YouTube",
    "Outlook",
    "Gmail",
    "Deepgram",
    "Cartesia",
    "Tailscale",
    # Tagalog the user actually speaks. Deliberately only words long enough
    # and distinct enough not to collide with English ones -- "po", "at" and
    # "ate" are left out for that reason.
    "salamat",
    "sige",
    "opo",
    "kuya",
    "talaga",
    "kumusta",
    "bahala",
    "naman",
)


def keyterms_path(config_dir: Optional[Path] = None) -> Path:
    return (config_dir or DEFAULT_CONFIG_DIR) / "keyterms.json"


def load_user_terms(config_dir: Optional[Path] = None) -> List[str]:
    """The user's own words, as edited in Settings."""
    try:
        data = json.loads(keyterms_path(config_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    terms = data.get("terms") if isinstance(data, dict) else data
    if not isinstance(terms, list):
        return []
    return [str(term) for term in terms]


def save_user_terms(terms: List[str], config_dir: Optional[Path] = None) -> List[str]:
    cleaned = clean(terms)
    path = keyterms_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"terms": cleaned}, indent=2), encoding="utf-8")
    return cleaned


def clean(terms: List[str]) -> List[str]:
    """Trim, drop what is too short or repeated, keep the order given."""
    out: List[str] = []
    seen = set()
    for raw in terms or []:
        term = re.sub(r"\s+", " ", str(raw or "")).strip()
        if len(term.replace(" ", "")) < MIN_LENGTH:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
    return out


_INSTRUCTOR_SUFFIX = re.compile(r",\s*(?:MS|MSc|PhD|MEng|RCE|CE|Engr\.?)\s*$", re.I)


def from_class_schedule(path: Optional[Path] = None) -> List[str]:
    """Instructor surnames and subject names out of the schedule file.

    Read from the local markdown rather than a tool call: this runs when the
    speech socket opens, and it must not wait on anything.
    """
    try:
        from openjarvis.tools.check_class_schedule import _default_schedule_path

        source = Path(path or _default_schedule_path())
        text = source.read_text(encoding="utf-8")
    except Exception:
        return []

    found: List[str] = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 8 or cells[0].lower().startswith("subject code"):
            continue
        # The subject's words carry the jargon ("Hydraulics", "Geotechnical");
        # the whole description is too long to boost as one phrase.
        for word in re.findall(r"[A-Za-z]{5,}", cells[1]):
            found.append(word)
        instructor = _INSTRUCTOR_SUFFIX.sub("", cells[7]).strip()
        if instructor:
            # The surname is what gets mangled; first names are ordinary.
            parts = [p for p in re.findall(r"[A-Za-z]{4,}", instructor)]
            if parts:
                found.append(parts[-1])
    return clean(found)


def all_terms(config_dir: Optional[Path] = None) -> List[str]:
    """Everything Deepgram should be told to expect, in priority order.

    Built-ins first so the name can never be crowded out by a long user list
    hitting the cap.
    """
    merged = clean(
        list(BUILT_IN) + load_user_terms(config_dir) + from_class_schedule()
    )
    return merged[:MAX_TERMS]


__all__ = [
    "BUILT_IN",
    "MAX_TERMS",
    "MIN_LENGTH",
    "all_terms",
    "clean",
    "from_class_schedule",
    "keyterms_path",
    "load_user_terms",
    "save_user_terms",
]
