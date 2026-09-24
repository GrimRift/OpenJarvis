"""Stopping Sage while it works takes back what its tools did (24 September)."""

from __future__ import annotations

import json
import threading
import time

from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.server.undo import TurnLedger, undo


class _Tool:
    def __init__(self):
        self.calls = []

    def execute(self, **params):
        self.calls.append(params)
        return ToolResult(tool_name="x", content="ok", success=True)


def _reminder_result(watch_id="w1"):
    return ToolResult(
        tool_name="tell_me_when",
        content="I'll tell you.",
        success=True,
        metadata={"watch": {"id": watch_id}},
    )


def _wait(predicate, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_a_reminder_set_before_the_stop_is_cancelled():
    tool = _Tool()
    ledger = TurnLedger(lambda name: tool if name == "tell_me_when" else None)
    call = ToolCall(id="1", name="tell_me_when", arguments="{}")
    ledger.run(lambda c: _reminder_result(), call)
    ledger.stop()
    assert _wait(lambda: tool.calls == [{"action": "cancel", "watch_id": "w1"}])


def test_a_tool_still_running_is_undone_when_it_finishes():
    tool = _Tool()
    ledger = TurnLedger(lambda name: tool)
    release = threading.Event()

    def slow(call):
        release.wait(2)
        return _reminder_result("w2")

    worker = threading.Thread(
        target=ledger.run,
        args=(slow, ToolCall(id="1", name="tell_me_when", arguments="{}")),
    )
    worker.start()
    ledger.stop()
    release.set()
    worker.join(2)
    assert tool.calls == [{"action": "cancel", "watch_id": "w2"}]


def test_nothing_is_undone_without_a_stop():
    tool = _Tool()
    ledger = TurnLedger(lambda name: tool)
    ledger.run(
        lambda c: _reminder_result(),
        ToolCall(id="1", name="tell_me_when", arguments="{}"),
    )
    time.sleep(0.05)
    assert tool.calls == []


def test_a_scheduled_task_is_cancelled_by_the_id_in_its_result():
    tool = _Tool()
    result = ToolResult(
        tool_name="schedule_task", content=json.dumps({"task_id": "t9"}), success=True
    )
    done = undo(
        ToolCall(id="1", name="schedule_task", arguments="{}"), result, lambda n: tool
    )
    assert done == "cancelled the scheduled task"
    assert tool.calls == [{"task_id": "t9"}]


def test_only_starting_music_is_undone_by_pausing():
    tool = _Tool()
    play = ToolCall(id="1", name="spotify_control", arguments='{"action": "play"}')
    status = ToolCall(id="2", name="spotify_control", arguments='{"action": "status"}')
    ok = ToolResult(tool_name="spotify_control", content="ok", success=True)
    assert undo(status, ok, lambda n: tool) is None
    assert undo(play, ok, lambda n: tool) == "paused the music"
    assert tool.calls == [{"action": "pause"}]


def test_a_failed_or_harmless_tool_has_nothing_to_undo():
    failed = ToolResult(tool_name="tell_me_when", content="no", success=False)
    assert undo(ToolCall(id="1", name="tell_me_when", arguments="{}"), failed) is None
    read = ToolResult(tool_name="gmail_read", content="mail", success=True)
    assert undo(ToolCall(id="2", name="gmail_read", arguments="{}"), read) is None
