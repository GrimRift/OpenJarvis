"""Tests for the notify_windows tool."""

from __future__ import annotations

from unittest.mock import patch

from openjarvis.core.registry import ToolRegistry


def test_notify_windows_registered():
    from openjarvis.tools.notify_windows import NotifyWindowsTool

    ToolRegistry.register_value("notify_windows", NotifyWindowsTool)
    assert ToolRegistry.contains("notify_windows")


def test_notify_windows_execute_success():
    from openjarvis.tools.notify_windows import NotifyWindowsTool

    tool = NotifyWindowsTool()

    with patch("openjarvis.tools.notify_windows._send_toast") as mock_send:
        result = tool.execute(title="Class starting soon", message="CECMPM1D in 10 min")

    mock_send.assert_called_once_with(
        "Class starting soon", "CECMPM1D in 10 min", duration="short"
    )
    assert result.success is True
    assert result.metadata["title"] == "Class starting soon"


def test_notify_windows_missing_title():
    from openjarvis.tools.notify_windows import NotifyWindowsTool

    tool = NotifyWindowsTool()
    result = tool.execute(title="", message="something")
    assert result.success is False


def test_notify_windows_missing_message():
    from openjarvis.tools.notify_windows import NotifyWindowsTool

    tool = NotifyWindowsTool()
    result = tool.execute(title="something", message="")
    assert result.success is False


def test_notify_windows_backend_exception_does_not_raise():
    from openjarvis.tools.notify_windows import NotifyWindowsTool

    tool = NotifyWindowsTool()

    with patch(
        "openjarvis.tools.notify_windows._send_toast",
        side_effect=RuntimeError("no toast backend"),
    ):
        result = tool.execute(title="Title", message="Message")

    assert result.success is False
    assert "no toast backend" in result.content
