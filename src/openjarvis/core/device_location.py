"""Where this machine currently is, via the Windows Location Service.

The morning briefing runs headless on a schedule, so there is no browser to
ask for coordinates and no user present to name a city. Reading the location
the operating system already knows lets the briefing follow the machine when
it moves, and falls back to the configured city whenever it cannot.

Windows-only by nature. Everywhere else this reports "unknown", which callers
must treat as "use the configured place" rather than as an error.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

Coordinates = Tuple[float, float]

#: A location fix costs about a second and the machine does not move between
#: two questions asked in the same minute. Long enough to keep a conversation
#: cheap, short enough that a drive across town is reflected by the time the
#: next briefing runs.
_CACHE_SECONDS = 300.0

#: The service can sit waiting on a fix indefinitely when wifi positioning has
#: nothing to work with. A briefing that blocks forever is worse than one that
#: uses the configured city, so the wait is bounded.
_FIX_TIMEOUT = 6.0

_lock = threading.Lock()
_cached: Optional[Coordinates] = None
_cached_at = 0.0


def _read_position(timeout: float) -> Optional[Coordinates]:
    """Ask Windows for a fix, on a private event loop.

    Run on its own thread with its own loop rather than through asyncio.run:
    callers include a FastAPI route and a scheduled job, and asyncio.run
    raises if a loop is already running in the calling thread.
    """
    result: list[Optional[Coordinates]] = [None]

    def worker() -> None:
        try:
            import asyncio

            from winsdk.windows.devices.geolocation import Geolocator

            async def fetch() -> Optional[Coordinates]:
                locator = Geolocator()
                position = await locator.get_geoposition_async()
                point = position.coordinate.point.position
                return (point.latitude, point.longitude)

            loop = asyncio.new_event_loop()
            try:
                result[0] = loop.run_until_complete(fetch())
            finally:
                loop.close()
        except Exception as exc:  # noqa: BLE001
            # Location off, consent withdrawn, no wifi fix, winsdk absent --
            # all mean the same thing to the caller, and none is worth an
            # exception when a configured city is sitting right there.
            logger.debug("Device location unavailable: %s", exc)

    thread = threading.Thread(target=worker, daemon=True, name="device-location")
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        logger.debug("Device location timed out after %.1fs", timeout)
        return None
    return result[0]


def current_coordinates(
    *, timeout: float = _FIX_TIMEOUT, max_age: float = _CACHE_SECONDS
) -> Optional[Coordinates]:
    """Latitude and longitude of this machine, or None if unknown."""
    if sys.platform != "win32":
        return None

    global _cached, _cached_at
    with _lock:
        if _cached is not None and (time.monotonic() - _cached_at) < max_age:
            return _cached

    fix = _read_position(timeout)
    if fix is None:
        return None

    with _lock:
        _cached = fix
        _cached_at = time.monotonic()
    return fix


def reset_cache() -> None:
    """Forget the last fix. For tests, and for an explicit re-locate."""
    global _cached, _cached_at
    with _lock:
        _cached = None
        _cached_at = 0.0


__all__ = ["Coordinates", "current_coordinates", "reset_cache"]
