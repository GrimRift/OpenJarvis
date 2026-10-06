"""Skip every skippable YouTube ad in Opera, not only the first one.

Until 6 October Sage skipped the pre-roll of a video it opened and left the
rest -- the mid-roll ads, and any ad on a video the user opened themselves.
The user's choices that evening: every YouTube tab in Opera, whoever opened
it; an ad without a Skip button is left to play (as before); skips are
logged, not announced.

A background thread watches Opera's debugging port: about once a second it
reads each YouTube watch tab's ad state and clicks Skip the moment it is
offered (the same trusted click and selectors as ``opera_control.skip_ad``).
It never starts Opera: no debugging port, nothing to watch.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import Any, Dict

logger = logging.getLogger(__name__)

#: Seconds between looks while a YouTube tab is open; a Skip button is
#: clicked within about this long of appearing.
POLL_SECONDS = 1.0
#: Seconds between looks with no YouTube tab, or with Opera closed.
IDLE_SECONDS = 3.0
_WATCH_URL = "youtube.com/watch"

_started = False
_start_lock = threading.Lock()


def _youtube_targets(browser: Any) -> Dict[str, Dict[str, Any]]:
    return {
        str(t.get("id") or ""): t
        for t in browser.page_targets()
        if _WATCH_URL in (t.get("url") or "")
    }


def check_once(browser: Any, pages: Dict[str, Any], skipping: set) -> int:
    """One look at every YouTube tab; returns how many ads were skipped.

    *pages* caches the attached tabs between looks (target id -> page) and
    *skipping* holds the tabs whose current ad was already logged.
    """
    from openjarvis.tools.opera_control import _AD_STATE_JS, _SKIP_SELECTOR

    targets = _youtube_targets(browser)
    for gone in [tid for tid in pages if tid not in targets]:
        with contextlib.suppress(Exception):
            pages.pop(gone).close()
        skipping.discard(gone)
    skipped = 0
    for tid, target in targets.items():
        page = pages.get(tid)
        if page is None:
            try:
                page = browser.attach(target)
            except Exception:  # noqa: BLE001 -- a tab mid-navigation
                continue
            pages[tid] = page
        try:
            raw = page.evaluate(_AD_STATE_JS)
        except Exception:  # noqa: BLE001 -- the socket went stale: re-attach
            with contextlib.suppress(Exception):
                pages.pop(tid).close()
            continue
        state = {
            "ad": bool(isinstance(raw, dict) and raw.get("ad")),
            "skip": bool(isinstance(raw, dict) and raw.get("skip")),
        }
        if not state["ad"]:
            skipping.discard(tid)
            continue
        if not state["skip"]:
            continue
        try:
            clicked = page.click(_SKIP_SELECTOR)
        except Exception:  # noqa: BLE001 -- the socket went stale: re-attach
            with contextlib.suppress(Exception):
                pages.pop(tid).close()
            continue
        if clicked and tid not in skipping:
            skipping.add(tid)
            skipped += 1
            logger.info(
                "Skipped a YouTube ad: %s", (target.get("title") or "")[:80]
            )
    return skipped


def _watch(stop: threading.Event) -> None:
    from openjarvis.tools.cdp import Browser
    from openjarvis.tools.opera_control import DEBUG_PORT, port_is_open

    pages: Dict[str, Any] = {}
    skipping: set = set()
    while not stop.is_set():
        wait = IDLE_SECONDS
        try:
            if port_is_open(timeout=0.5):
                check_once(Browser(DEBUG_PORT, timeout=5.0), pages, skipping)
                if pages:
                    wait = POLL_SECONDS
            elif pages:
                for page in pages.values():
                    with contextlib.suppress(Exception):
                        page.close()
                pages.clear()
        except Exception:  # noqa: BLE001 -- never let the watcher die
            logger.debug("YouTube ad watch failed a look", exc_info=True)
        stop.wait(wait)


def start() -> threading.Event | None:
    """Start the watcher once per process; returns its stop event."""
    global _started
    with _start_lock:
        if _started:
            return None
        _started = True
    stop = threading.Event()
    threading.Thread(
        target=_watch, args=(stop,), name="youtube-ad-skip", daemon=True
    ).start()
    return stop


__all__ = ["IDLE_SECONDS", "POLL_SECONDS", "check_once", "start"]
