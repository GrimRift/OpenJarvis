"""Sites that would not let web_read in, remembered for a week.

mb.com.ph answers every automated request with a Cloudflare "Just a moment..."
check. The plain request sees it at once, the search provider's reader gets
nothing either, and the browser then sits at the same check until its 10 s cap:
every research turn that picked it paid those 10 s for no text (6 October).
A host is written here after such a read, and later reads of it are skipped
straight away, with the search results saying so up front so the model picks
another page instead.

A bot check counts at once. A browser timeout only after a second one, since
Opera itself is sometimes slow to answer and that is no fault of the site.
Entries expire after ``KEEP_SECONDS`` so a site that drops its wall is tried
again. Kept in ``<data>/unreadable_hosts.json`` across restarts.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from openjarvis.core.paths import get_config_dir

logger = logging.getLogger(__name__)

KEEP_SECONDS = 7 * 24 * 3600
TIMEOUTS_TO_MARK = 2

_lock = threading.Lock()
_test_path: Optional[Path] = None
_loaded: Optional[Dict[str, Dict[str, Any]]] = None


def use_path_for_tests(path: Optional[Path]) -> None:
    global _test_path, _loaded
    with _lock:
        _test_path = path
        _loaded = None


def _path() -> Path:
    return _test_path or get_config_dir() / "unreadable_hosts.json"


def host_of(url: str) -> str:
    host = (urlparse(url if "//" in url else "https://" + url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _table() -> Dict[str, Dict[str, Any]]:
    global _loaded
    if _loaded is None:
        try:
            data = json.loads(_path().read_text(encoding="utf-8"))
            _loaded = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _loaded = {}
    now = time.time()
    for host in [h for h, e in _loaded.items() if e.get("until", 0) < now]:
        del _loaded[host]
    return _loaded


def _save(table: Dict[str, Dict[str, Any]]) -> None:
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(table, indent=1), encoding="utf-8")
    except OSError:
        logger.debug("unreadable_hosts: could not save", exc_info=True)


def reason_for(url: str) -> str:
    """Why *url*'s site cannot be read ("bot check", "too slow"), or ""."""
    host = host_of(url)
    if not host:
        return ""
    with _lock:
        entry = _table().get(host)
    if not entry or not entry.get("blocked"):
        return ""
    return str(entry.get("reason") or "bot check")


def mark(url: str, reason: str) -> None:
    """Record that *url*'s site blocked a read. ``reason`` "timeout" needs
    ``TIMEOUTS_TO_MARK`` strikes; anything else blocks at once."""
    host = host_of(url)
    if not host:
        return
    with _lock:
        table = _table()
        entry = table.get(host) or {"strikes": 0}
        entry["strikes"] = int(entry.get("strikes", 0)) + 1
        entry["until"] = time.time() + KEEP_SECONDS
        if reason != "timeout" or entry["strikes"] >= TIMEOUTS_TO_MARK:
            if not entry.get("blocked"):
                logger.info("web_read: %s marked unreadable (%s)", host, reason)
            entry["blocked"] = True
            entry["reason"] = "bot check" if reason != "timeout" else "too slow"
        table[host] = entry
        _save(table)


def clear(url: str) -> None:
    """Forget *url*'s site after a read of it worked."""
    host = host_of(url)
    with _lock:
        table = _table()
        if table.pop(host, None) is not None:
            _save(table)
