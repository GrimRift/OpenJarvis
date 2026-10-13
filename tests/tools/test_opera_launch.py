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


def _frozen_opera(monkeypatch, *, windows_hung: bool = True, port: bool = False):
    """An Opera that is running, its windows hung, its port dead (13 October)."""
    state = {"running": True, "killed": 0, "launches": 0, "launched_at": None}

    def port_is_open(timeout: float = 1.5) -> bool:
        if state["launched_at"] is not None:
            return time.monotonic() - state["launched_at"] > 0.1
        return port

    def close() -> bool:
        if not opera_control.opera_frozen():
            return False
        state["killed"] += 1
        state["running"] = False
        return True

    def launch(minimized: bool):
        state["launches"] += 1
        state["running"] = True
        state["launched_at"] = time.monotonic()
        return None

    monkeypatch.setattr(opera_control, "port_is_open", port_is_open)
    monkeypatch.setattr(opera_control, "_opera_running", lambda: state["running"])
    monkeypatch.setattr(opera_control, "_opera_windows_hung", lambda: windows_hung)
    monkeypatch.setattr(opera_control, "_close_frozen_opera", close)
    monkeypatch.setattr(opera_control, "_launch_opera", launch)
    monkeypatch.setattr(opera_control, "_first_window_ready", lambda: True)
    return state


def test_a_frozen_opera_is_restarted(monkeypatch):
    """Frozen used to read as "open without the setting"; the user's choice is
    that Sage restarts it."""
    state = _frozen_opera(monkeypatch)
    assert opera_control.ensure_opera(minimized=True) is None
    assert (state["killed"], state["launches"]) == (1, 1)


def test_an_opera_without_the_port_is_still_left_alone(monkeypatch):
    state = _frozen_opera(monkeypatch, windows_hung=False)
    problem = opera_control.ensure_opera(minimized=True)
    assert problem and "without the setting" in problem
    assert (state["killed"], state["launches"]) == (0, 0)


def test_hung_windows_with_a_live_port_are_not_frozen(monkeypatch):
    """A heavy page can stall the windows while the browser still answers;
    killing it would close every tab the user has for nothing."""
    _frozen_opera(monkeypatch, port=True)
    assert opera_control.opera_frozen() is False


def test_an_outlook_read_that_hangs_retries_after_a_restart(monkeypatch):
    from openjarvis.tools.opera_control import OutlookReadTool

    calls = {"reads": 0, "recovered": 0}

    def read(self, url, count):
        calls["reads"] += 1
        if calls["reads"] == 1:
            raise RuntimeError("Runtime.evaluate failed: ")
        return [("Inbox", ["[received today] Someone | Subject"], None)]

    def recover(minimized: bool = True) -> bool:
        calls["recovered"] += 1
        return True

    monkeypatch.setattr(opera_control, "ensure_opera", lambda minimized=False: None)
    monkeypatch.setattr(OutlookReadTool, "_read_inbox", read)
    monkeypatch.setattr(opera_control, "recover_frozen_opera", recover)
    result = OutlookReadTool().execute(count=5)
    assert result.success, result.content
    assert calls == {"reads": 2, "recovered": 1}


def test_an_outlook_read_that_fails_without_a_freeze_is_not_retried(monkeypatch):
    from openjarvis.tools.opera_control import OutlookReadTool

    calls = {"reads": 0}

    def read(self, url, count):
        calls["reads"] += 1
        raise RuntimeError("Runtime.evaluate failed: ")

    monkeypatch.setattr(opera_control, "ensure_opera", lambda minimized=False: None)
    monkeypatch.setattr(OutlookReadTool, "_read_inbox", read)
    monkeypatch.setattr(
        opera_control, "recover_frozen_opera", lambda minimized=True: False
    )
    result = OutlookReadTool().execute(count=5)
    assert not result.success and "could not read the inbox" in result.content
    assert calls["reads"] == 1
