"""The pages Sage opens must render at full speed, and let go when it leaves.

On 17 September a video Sage opened played at about five frames a second
while the same video in the user's own tab was smooth. The cause was the
``prefers-color-scheme`` emulation Sage set for dark mode: a page under a
DevTools emulation renders on a slow path, and the emulation outlived the
call whenever the socket close stalled. Dark mode now goes through
YouTube's own preference cookie, and a stalled close aborts the transport.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from openjarvis.tools import cdp
from openjarvis.tools.cdp import YOUTUBE_DARK_BIT, Page, youtube_dark_pref

TOOLS = Path(cdp.__file__).parent


class TestTheDarkBit:
    def test_sets_the_bit_and_keeps_the_rest(self):
        assert (
            youtube_dark_pref("tz=Asia.Manila&f7=100&f6=40000000&f4=10000")
            == "tz=Asia.Manila&f7=100&f6=40000400&f4=10000"
        )

    def test_already_dark_is_unchanged(self):
        value = "tz=Asia.Manila&f6=40000400"
        assert youtube_dark_pref(value) == value

    def test_no_cookie_becomes_just_the_theme(self):
        assert youtube_dark_pref("") == f"f6={YOUTUBE_DARK_BIT:x}"
        assert youtube_dark_pref("tz=Asia.Manila") == "tz=Asia.Manila&f6=400"

    def test_garbage_in_f6_is_replaced_not_crashed_on(self):
        assert youtube_dark_pref("f6=zz") == "f6=400"


class _Connection:
    def __init__(self, cookies):
        self.cookies = cookies
        self.calls = []

    def send(self, method, params=None):
        self.calls.append((method, params or {}))
        if method == "Network.getCookies":
            return {"cookies": self.cookies}
        return {}

    def close(self):
        return None


class TestPreferDarkYoutube:
    def test_writes_the_cookie_with_the_bit_set(self):
        connection = _Connection(
            [{"name": "PREF", "value": "tz=Asia.Manila&f6=40000000", "expires": 99.0}]
        )
        Page(connection, "t1").prefer_dark_youtube()
        methods = [m for m, _ in connection.calls]
        assert "Network.setCookie" in methods
        _, cookie = next(c for c in connection.calls if c[0] == "Network.setCookie")
        assert cookie["value"] == "tz=Asia.Manila&f6=40000400"
        assert (cookie["domain"], cookie["path"], cookie["expires"]) == (
            ".youtube.com",
            "/",
            99.0,
        )

    def test_leaves_a_cookie_that_is_already_dark_alone(self):
        connection = _Connection([{"name": "PREF", "value": "f6=400"}])
        Page(connection, "t1").prefer_dark_youtube()
        assert "Network.setCookie" not in [m for m, _ in connection.calls]

    def test_never_uses_an_emulation(self):
        """The whole point: no ``Emulation.*`` call anywhere in the browser
        tools. An emulated media feature put a video on a slow render path."""
        offenders = []
        for path in TOOLS.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"[\"']Emulation\.\w+[\"']", text):
                offenders.append(f"{path.name}: {match.group(0)}")
        assert offenders == []


class _StallingSocket:
    """A websocket whose polite close never completes."""

    def __init__(self):
        self.aborted = False

        class _Transport:
            def abort(inner):
                self.aborted = True

        self.transport = _Transport()

    async def close(self):
        await asyncio.sleep(60)


def test_a_close_that_stalls_aborts_the_transport(monkeypatch):
    connection = cdp.Connection.__new__(cdp.Connection)
    connection._loop = asyncio.new_event_loop()
    connection._socket = _StallingSocket()
    monkeypatch.setattr(cdp, "_CLOSE_TIMEOUT", 0.05)
    connection.close()
    assert connection._socket.aborted
