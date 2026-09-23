"""Choosing which YouTube result to play.

``youtube_play`` used to play the first result, and the first result is
YouTube's guess, not the user's request: a live stream when they asked for a
mix, a Short, a video that shares one word with what they said. The results
page already says what each video is -- title, channel, length, views, age,
LIVE -- in the data it is built from, and a plain request returns it in under
a second (0.77 s, 24 September), faster than drawing the page in the browser.

So the results are read, scored against the user's own words, and the best
one is played; the next few are reported so "the other one" can be played
without searching again. Pure, so the scoring is tested without a network.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional

_MARKER = "var ytInitialData = "

#: Words that say nothing about which video is meant.
_STOPWORDS = frozenset(
    """a an the of to for and or in on at by with from about me my i you your
    play put open watch show find some something video videos youtube yt
    please can could would sage sir hey one that this is it be want wanna
    like just""".split()
)

#: Words asking for a kind of video the scoring otherwise steers away from.
_WANTS_LIVE = frozenset({"live", "stream", "streaming", "radio", "livestream"})
_WANTS_SHORT = frozenset({"short", "shorts", "clip", "trailer", "teaser"})
_WANTS_LONG = frozenset(
    {
        "mix",
        "compilation",
        "hour",
        "hours",
        "full",
        "playlist",
        "album",
        "podcast",
        "episode",
        "documentary",
        "movie",
        "lecture",
        "course",
        "study",
        "sleep",
        "ambient",
        "lofi",
        "focus",
        "relax",
        "relaxing",
    }
)


@dataclass
class Candidate:
    video_id: str
    title: str
    channel: str = ""
    seconds: Optional[int] = None
    views: Optional[int] = None
    age: str = ""
    live: bool = False
    position: int = 0
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)

    @property
    def href(self) -> str:
        return f"/watch?v={self.video_id}"

    def describe(self) -> str:
        parts = [repr(self.title)]
        if self.channel:
            parts.append(f"by {self.channel}")
        if self.live:
            parts.append("LIVE")
        elif self.seconds is not None:
            parts.append(_clock(self.seconds))
        if self.views is not None:
            parts.append(f"{_short_count(self.views)} views")
        if self.age:
            parts.append(self.age)
        return ", ".join(parts)


def parse_results(markup: str) -> List[Candidate]:
    """The videos on a YouTube results page, in YouTube's order."""
    start = markup.find(_MARKER)
    if start < 0:
        return []
    try:
        data, _ = json.JSONDecoder().raw_decode(markup, start + len(_MARKER))
    except ValueError:
        return []
    found: List[Candidate] = []
    seen: set[str] = set()
    for renderer in _video_renderers(data):
        video_id = renderer.get("videoId")
        if not isinstance(video_id, str) or video_id in seen:
            continue
        seen.add(video_id)
        badges = " ".join(
            str((badge.get("metadataBadgeRenderer") or {}).get("label") or "")
            for badge in renderer.get("badges") or []
            if isinstance(badge, dict)
        ).upper()
        views_text = _text(renderer.get("viewCountText"))
        found.append(
            Candidate(
                video_id=video_id,
                title=_text(renderer.get("title")),
                channel=_text(renderer.get("ownerText")),
                seconds=_seconds(_text(renderer.get("lengthText"))),
                views=_count(views_text),
                age=_text(renderer.get("publishedTimeText")),
                live="LIVE" in badges or "watching" in views_text.lower(),
                position=len(found),
            )
        )
    return found


def rank(candidates: Iterable[Candidate], request: str) -> List[Candidate]:
    """*candidates* best first for what *request* asks for."""
    words = _words(request)
    live_words = {_stem(word) for word in _WANTS_LIVE}
    short_words = {_stem(word) for word in _WANTS_SHORT}
    wants_live = bool(words & live_words)
    wants_short = bool(words & short_words)
    wants_long = bool(words & {_stem(word) for word in _WANTS_LONG})
    wanted = words - live_words - short_words
    request_flat = _flat(request)
    ranked: List[Candidate] = []
    for candidate in candidates:
        reasons: List[str] = []
        title_words = _words(candidate.title)
        channel_words = _words(candidate.channel)
        score = 0.0
        if wanted:
            in_title = len(wanted & title_words) / len(wanted)
            in_either = len(wanted & (title_words | channel_words)) / len(wanted)
            score += 4.0 * in_title + 2.0 * in_either
        channel_flat = _flat(candidate.channel)
        if len(channel_flat) >= 4 and channel_flat in request_flat:
            score += 3.0
            reasons.append("the channel you named")
        if candidate.live and not wants_live:
            score -= 2.5
        elif candidate.live and wants_live:
            score += 1.5
            reasons.append("live")
        if candidate.seconds is not None:
            if candidate.seconds < 70 and not wants_short:
                score -= 2.0  # a Short or a snippet
            if wants_long and candidate.seconds >= 1800:
                score += 1.0
                reasons.append("a long one")
            if wants_short and candidate.seconds <= 240:
                score += 1.0
        if candidate.views:
            score += 0.15 * math.log10(candidate.views + 1)
        # YouTube's own order, as the tie-break it is good at.
        score -= 0.25 * candidate.position
        candidate.score = round(score, 3)
        candidate.reasons = reasons
        ranked.append(candidate)
    ranked.sort(key=lambda c: (-c.score, c.position))
    return ranked


_SEARCH_URL = "https://www.youtube.com/results?search_query="
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)


def search(query: str, timeout: float = 5.0) -> List[Candidate]:
    """YouTube's results for *query*, read without the browser. Empty on
    any failure: the caller falls back to the results page in Opera."""
    import urllib.parse

    import httpx

    try:
        response = httpx.get(
            _SEARCH_URL + urllib.parse.quote_plus(query),
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=timeout,
            follow_redirects=True,
        )
        if response.status_code != 200:
            return []
        return parse_results(response.text)
    except Exception:  # noqa: BLE001
        return []


def _video_renderers(node: Any) -> Iterable[dict]:
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            renderer = item.get("videoRenderer")
            if isinstance(renderer, dict):
                yield renderer
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))


def _text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    if isinstance(value.get("simpleText"), str):
        return value["simpleText"]
    runs = value.get("runs") or []
    return "".join(str(run.get("text") or "") for run in runs if isinstance(run, dict))


def _seconds(text: str) -> Optional[int]:
    if not re.fullmatch(r"\d+(:\d{1,2}){1,2}", text or ""):
        return None
    total = 0
    for part in text.split(":"):
        total = total * 60 + int(part)
    return total


def _count(text: str) -> Optional[int]:
    digits = re.sub(r"[^\d]", "", (text or "").split(" ")[0])
    return int(digits) if digits else None


def _words(text: str) -> set[str]:
    return {
        _stem(word)
        for word in re.findall(r"[a-z0-9]+", (text or "").lower())
        if word not in _STOPWORDS and len(word) > 1
    }


def _stem(word: str) -> str:
    """ "holes" and "hole" are one word to a listener."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _flat(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _clock(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _short_count(value: int) -> str:
    for size, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if value >= size:
            return f"{value / size:.1f}".rstrip("0").rstrip(".") + suffix
    return str(value)


__all__ = ["Candidate", "parse_results", "rank", "search"]
