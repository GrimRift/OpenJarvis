"""Starting Opera for reads that run at the same time (29 September).

The briefing reads Teams and Outlook in parallel. With Opera closed, both saw
no control port and both launched it; the second launch opened a new window
in the starting Opera, the reads' tabs landed there, and the window stayed
behind after they closed. The first Outlook read also failed.
"""

from __future__ import annotations

import threading
import time

from openjarvis.tools import opera_control


def _fake_opera(monkeypatch, *, window_after: float = 0.0):
    state = {"launches": 0, "launched_at": None}

    def port_is_open(timeout: float = 1.5) -> bool:
        return (
            state["launched_at"] is not None
            and time.monotonic() - state["launched_at"] > 0.2
        )

    def running() -> bool:
        return state["launched_at"] is not None

    def launch(minimized: bool):
        state["launches"] += 1
        state["launched_at"] = time.monotonic()
        return None

    def window_ready() -> bool:
        return (
            state["launched_at"] is not None
            and time.monotonic() - state["launched_at"] > window_after
        )

    monkeypatch.setattr(opera_control, "port_is_open", port_is_open)
    monkeypatch.setattr(opera_control, "_opera_running", running)
    monkeypatch.setattr(opera_control, "_launch_opera", launch)
    monkeypatch.setattr(
        opera_control, "_first_window_ready", window_ready, raising=False
    )
    return state


def test_two_reads_at_once_start_opera_once(monkeypatch):
    state = _fake_opera(monkeypatch)
    results: list = []
    def read() -> None:
        results.append(opera_control.ensure_opera(minimized=True))

    threads = [threading.Thread(target=read) for _ in range(2)]
    for thread in threads:
        thread.start()
        time.sleep(0.05)  # the second arrives while the first is launching
    for thread in threads:
        thread.join(10)
    assert state["launches"] == 1
    assert results == [None, None]


def test_tabs_wait_for_operas_own_window(monkeypatch):
    """A tab opened before Opera's first window exists gets a window of its
    own; wait for the first window after launching."""
    _fake_opera(monkeypatch, window_after=0.6)
    began = time.monotonic()
    assert opera_control.ensure_opera(minimized=True) is None
    assert time.monotonic() - began >= 0.6


def test_readers_outlast_a_cold_opera_start():
    """30 September, 02:14: a scheduled brief found Opera closed and Outlook
    was cut off at the executor's default 30 s -- launching Opera (up to 30 s)
    and the tripled first page load (75 s) need more than that."""
    from openjarvis.tools.opera_control import OutlookReadTool
    from openjarvis.tools.teams_read import TeamsReadTool

    cold = opera_control._LAUNCH_WAIT_SECONDS + 3 * opera_control._NAV_TIMEOUT
    for tool in (OutlookReadTool(), TeamsReadTool()):
        assert tool.spec.timeout_seconds > cold, tool.spec.name
