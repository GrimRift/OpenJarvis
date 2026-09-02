"""A minimal synchronous Chrome DevTools Protocol client.

Written to replace Playwright in ``opera_control``, for one specific reason:
``connect_over_cdp`` attaches to *every* target in the browser. The user's
Opera GX carries around 37 of them — Speed Dial, Easy Setup GX, the address-bar
dropdown, extension background pages, Facebook iframes — and attaching to that
set intermittently hung forever, or died with "Connection closed while reading
from the driver". Verified live: the websocket connected and then never
finished enumerating targets.

Nothing here needs the whole browser. Everything the media tools do happens on
*one page*, so this connects straight to that page's own websocket. Other tabs
cannot stall it because it never speaks to them, and there is no Node driver
subprocess in the picture at all.

Deliberately small: navigate, evaluate, poll, type. Anything richer belongs in
Playwright, and if this file starts growing that is the signal to reconsider.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Any, Dict, List, Optional

#: A close handshake is a courtesy, not a requirement — the browser drops the
#: target either way, so this never blocks a call for long.
_CLOSE_TIMEOUT = 1.5


class CDPError(RuntimeError):
    """Any failure talking to the browser."""


def _http_text(url: str, timeout: float = 5.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _http_json(url: str, timeout: float = 5.0) -> Any:
    return json.loads(_http_text(url, timeout))


class Connection:
    """One websocket, driven synchronously.

    Owns a private event loop rather than borrowing the caller's: tool code is
    synchronous and may run on any worker thread, and reusing an ambient loop
    is the kind of thing that works until two calls overlap.
    """

    def __init__(self, websocket_url: str, timeout: float = 20.0) -> None:
        import websockets

        self._timeout = timeout
        self._next_id = 0
        self._loop = asyncio.new_event_loop()
        try:
            self._socket = self._run(
                websockets.connect(
                    websocket_url, max_size=None, ping_interval=None
                )
            )
        except Exception as error:  # pragma: no cover — network surface
            self._loop.close()
            raise CDPError(f"could not open {websocket_url}: {error}") from error

    def _run(self, coroutine):
        return self._loop.run_until_complete(
            asyncio.wait_for(coroutine, timeout=self._timeout)
        )

    def send(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict:
        """Issue *method* and return its result, skipping any events in between."""
        self._next_id += 1
        message_id = self._next_id
        payload = json.dumps(
            {"id": message_id, "method": method, "params": params or {}}
        )
        try:
            self._run(self._socket.send(payload))
            while True:
                raw = self._run(self._socket.recv())
                message = json.loads(raw)
                # Events carry no "id"; they are not what this call asked for.
                if message.get("id") != message_id:
                    continue
                if "error" in message:
                    raise CDPError(f"{method}: {message['error'].get('message')}")
                return message.get("result") or {}
        except CDPError:
            raise
        except Exception as error:
            raise CDPError(f"{method} failed: {error}") from error

    def close(self) -> None:
        """Drop the socket without waiting on a polite close handshake.

        Closing used to reuse the request timeout, so a socket the browser was
        slow to release held the call for up to 20 seconds — three of them
        turned a 5-second Teams read into 15. Nothing here needs the handshake
        to complete: the browser tears the target down regardless.

        Pending reads are cancelled and drained before the loop goes, because
        closing a proactor loop with a read in flight logs "Cancelling an
        overlapped future failed" and leaves the traceback in Sage's output.
        """
        try:
            self._loop.run_until_complete(
                asyncio.wait_for(self._socket.close(), timeout=_CLOSE_TIMEOUT)
            )
        except Exception:
            pass
        try:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except Exception:
            pass
        try:
            self._loop.close()
        except Exception:
            pass


class Page:
    """One browser tab, addressed directly."""

    def __init__(self, connection: Connection, target_id: str) -> None:
        self._connection = connection
        self.target_id = target_id
        self._connection.send("Page.enable")
        self._connection.send("Runtime.enable")

    def close(self) -> None:
        self._connection.close()

    def emulate_dark(self) -> None:
        with _ignored():
            self._connection.send(
                "Emulation.setEmulatedMedia",
                {
                    "features": [
                        {"name": "prefers-color-scheme", "value": "dark"}
                    ]
                },
            )

    def evaluate(self, expression: str) -> Any:
        """Evaluate *expression* and return a plain Python value.

        ``awaitPromise`` is on so an expression may be async; ``returnByValue``
        because a remote object handle is of no use to a caller that only ever
        wants data back.
        """
        result = self._connection.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        details = result.get("exceptionDetails")
        if details:
            raise CDPError(details.get("text") or "evaluation failed")
        return (result.get("result") or {}).get("value")

    def navigate(self, url: str, timeout: float = 25.0) -> None:
        result = self._connection.send("Page.navigate", {"url": url})
        if result.get("errorText"):
            raise CDPError(f"could not open {url}: {result['errorText']}")
        self.wait_for("document.readyState === 'complete'", timeout=timeout)

    def wait_for(self, expression: str, timeout: float = 12.0) -> bool:
        """Poll *expression* until it is truthy. Returns whether it became so.

        Polling rather than waiting on lifecycle events: the conditions that
        matter here are "has the results grid rendered", which no event
        announces, and one mechanism for all of them is easier to trust than
        two.
        """
        deadline = _now() + timeout
        while _now() < deadline:
            try:
                if self.evaluate(f"!!({expression})"):
                    return True
            except CDPError:
                pass
            self.sleep(0.25)
        return False

    def sleep(self, seconds: float) -> None:
        self._connection._run(asyncio.sleep(seconds))

    def title(self) -> str:
        try:
            return self.evaluate("document.title") or ""
        except CDPError:
            return ""

    def url(self) -> str:
        try:
            return self.evaluate("location.href") or ""
        except CDPError:
            return ""

    def press(self, key: str) -> None:
        """Send a real key press.

        A dispatched key event is a trusted gesture as far as the page is
        concerned, which matters: a scripted ``video.play()`` can be refused by
        autoplay policy where a keystroke is not.
        """
        for event_type in ("keyDown", "keyUp"):
            self._connection.send(
                "Input.dispatchKeyEvent",
                {
                    "type": event_type,
                    "text": key if event_type == "keyDown" else "",
                    "key": key,
                    "windowsVirtualKeyCode": ord(key.upper()),
                    "nativeVirtualKeyCode": ord(key.upper()),
                },
            )


class Browser:
    """The browser endpoint — used only to make windows and list targets."""

    def __init__(self, port: int, timeout: float = 20.0) -> None:
        self._port = port
        self._timeout = timeout

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def targets(self) -> List[Dict[str, Any]]:
        try:
            return _http_json(f"{self.base}/json/list") or []
        except Exception as error:
            raise CDPError(f"could not list browser tabs: {error}") from error

    def page_targets(self) -> List[Dict[str, Any]]:
        return [
            target
            for target in self.targets()
            if target.get("type") == "page" and target.get("webSocketDebuggerUrl")
        ]

    def attach(self, target: Dict[str, Any]) -> Page:
        connection = Connection(
            target["webSocketDebuggerUrl"], timeout=self._timeout
        )
        return Page(connection, target.get("id") or "")

    def attach_by_url(self, needle: str) -> Optional[Page]:
        """Attach to any target whose URL contains *needle*, iframes included.

        Teams renders Assignments in a cross-origin iframe, so the parent page
        cannot reach into it — ``contentDocument`` is blocked. Chromium gives
        that iframe its own debuggable target, which can be addressed directly.
        """
        for target in self.targets():
            if needle in (target.get("url") or "") and target.get(
                "webSocketDebuggerUrl"
            ):
                with _ignored():
                    return self.attach(target)
        return None

    def attach_by_id(self, target_id: str) -> Optional[Page]:
        for target in self.page_targets():
            if target.get("id") == target_id:
                return self.attach(target)
        return None

    def close_target(self, target_id: str) -> None:
        """Close one tab. A single-tab window closes with it."""
        if not target_id:
            return
        # Answers "Target is closing" as plain text, not JSON.
        with _ignored():
            _http_text(f"{self.base}/json/close/{target_id}")

    def new_tab(self) -> Page:
        """Create an ordinary tab in the existing window."""
        return self._create(new_window=False)

    def new_window(self) -> Page:
        """Create a separate browser window and attach to it.

        ``newWindow`` is the only way to get a window rather than a tab, and a
        tab cannot be moved between monitors on its own.
        """
        return self._create(new_window=True)

    def _create(self, *, new_window: bool) -> Page:
        try:
            version = _http_json(f"{self.base}/json/version")
            browser_ws = version["webSocketDebuggerUrl"]
        except Exception as error:
            raise CDPError(f"browser is not reachable: {error}") from error
        connection = Connection(browser_ws, timeout=self._timeout)
        try:
            result = connection.send(
                "Target.createTarget",
                {"url": "about:blank", "newWindow": new_window},
            )
            target_id = result.get("targetId") or ""
        finally:
            connection.close()
        if not target_id:
            raise CDPError("the browser did not create a tab")
        for _ in range(30):
            page = self.attach_by_id(target_id)
            if page is not None:
                return page
            _blocking_sleep(0.1)
        raise CDPError("the new tab never appeared")


class _ignored:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return True


def _now() -> float:
    import time

    return time.monotonic()


def _blocking_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


__all__ = ["Browser", "CDPError", "Connection", "Page"]
