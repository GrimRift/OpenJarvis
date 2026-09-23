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
* names learned from what Sage remembers -- the people, channels, games and
  programs the user talks about. A friend's words came back wrong often
  enough to rate the voice 5/10 (24 September), and the names that matter
  are already in memory: Kurzgesagt, AutoCAD, Revit, Arthur Nery.

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
MAX_TERMS = 80

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


#: Most names taken from memory: the rest of the cap stays for what the user
#: chose and the schedule.
MAX_LEARNED = 20

#: Capitalised in any sentence, never names.
_NOT_NAMES = frozenset(
    """sage sir mark monday tuesday wednesday thursday friday saturday sunday
    mondays tuesdays wednesdays thursdays fridays saturdays sundays january
    february march april june july august september october november december
    english filipino tagalog philippines philippine user today tomorrow""".split()
)

_WORD = r"[A-Z][a-z]+(?:[A-Z][A-Za-z]*)?"  # "Revit", "AutoCAD", "OpenJarvis"
# Up to four capitalised words, joined as titles are ("Lord of the
# Mysteries"), and a trailing number ("Formula 1"). Split, "Lord" alone would
# be boosted everywhere.
_NAME = re.compile(
    rf"\b{_WORD}(?:\s+(?:(?:of|the)\s+)*{_WORD}){{0,3}}(?:\s+\d{{1,2}}\b)?"
)


def from_memory(
    config_dir: Optional[Path] = None, known: Optional[List[str]] = None
) -> List[str]:
    """Names the user talks about, from the facts Sage remembers.

    A word counts as a name when it is capitalised mid-sentence and never
    written in lower case anywhere in memory -- "Project" and "Course" are
    both, "Kurzgesagt" and "Revit" never are. Private and removed facts are
    left out: the list is sent to Deepgram. Most frequent first.
    """
    path = (config_dir or DEFAULT_CONFIG_DIR) / "memory_facts.jsonl"
    texts: List[str] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    fact = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(fact, dict):
                    continue
                if fact.get("private") or fact.get("removed_at"):
                    continue
                text = str(fact.get("text") or "")
                if text:
                    texts.append(text)
    except OSError:
        return []
    lower_words = {
        word for text in texts for word in re.findall(r"\b[a-z][a-z]+\b", text)
    }
    counts: dict[str, int] = {}
    order: List[str] = []
    for text in texts:
        for sentence in re.split(r"(?<=[.!?;:])\s+|\n+", text):
            for match in _NAME.finditer(sentence):
                if match.start() == 0:
                    continue  # the first word of a sentence is capitalised anyway
                name = match.group(0)
                words = [word for word in name.split() if word[:1].isupper()]
                if any(word.lower() in _NOT_NAMES for word in words):
                    continue
                if any(word.lower() in lower_words for word in words):
                    continue
                if name not in counts:
                    order.append(name)
                counts[name] = counts.get(name, 0) + 1
    ranked = sorted(order, key=lambda name: (-counts[name], order.index(name)))
    # Names already boosted from elsewhere would only use up the slots.
    listed = {term.lower() for term in known or []}
    return [name for name in clean(ranked) if name.lower() not in listed][
        :MAX_LEARNED
    ]


def all_terms(config_dir: Optional[Path] = None) -> List[str]:
    """Everything Deepgram should be told to expect, in priority order.

    Built-ins first so the name can never be crowded out by a long user list
    hitting the cap; names learned from memory last, since they are guesses.
    """
    chosen = list(BUILT_IN) + load_user_terms(config_dir) + from_class_schedule()
    merged = clean(chosen + from_memory(config_dir, known=chosen))
    return merged[:MAX_TERMS]


__all__ = [
    "BUILT_IN",
    "MAX_TERMS",
    "MIN_LENGTH",
    "all_terms",
    "clean",
    "from_class_schedule",
    "from_memory",
    "keyterms_path",
    "load_user_terms",
    "save_user_terms",
]
