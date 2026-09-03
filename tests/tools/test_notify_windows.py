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


# -- Delivery fan-out --------------------------------------------------------


class TestDeliver:
    """A desktop toast is useless away from the machine, which is exactly when
    a proactive notification matters — so a channel is an addition, not a
    replacement, and either destination arriving counts as delivered."""

    @staticmethod
    def _config(channel: str = "", desktop: bool = True):
        from unittest.mock import MagicMock

        cfg = MagicMock()
        cfg.notifications.channel = channel
        cfg.notifications.desktop = desktop
        return patch("openjarvis.core.config.load_config", return_value=cfg)

    def test_desktop_only_when_no_channel_configured(self):
        from openjarvis.tools.notify_windows import deliver

        with self._config(channel=""):
            with patch("openjarvis.tools.notify_windows._send_toast") as toast:
                assert deliver("t", "m") == ["desktop"]
        toast.assert_called_once()

    def test_both_when_a_channel_is_configured(self):
        from openjarvis.tools.notify_windows import deliver

        with self._config(channel="telegram:123"):
            with (
                patch("openjarvis.tools.notify_windows._send_toast"),
                patch(
                    "openjarvis.tools.notify_windows._send_to_channel",
                    return_value=True,
                ),
            ):
                assert deliver("t", "m") == ["desktop", "channel"]

    def test_channel_still_delivers_when_the_desktop_toast_fails(self):
        """The whole point: a muted laptop must not lose the phone alert."""
        from openjarvis.tools.notify_windows import deliver

        with self._config(channel="telegram:123"):
            with (
                patch(
                    "openjarvis.tools.notify_windows._send_toast",
                    side_effect=RuntimeError("no toast"),
                ),
                patch(
                    "openjarvis.tools.notify_windows._send_to_channel",
                    return_value=True,
                ),
            ):
                assert deliver("t", "m") == ["channel"]

    def test_desktop_still_delivers_when_the_channel_fails(self):
        from openjarvis.tools.notify_windows import deliver

        with self._config(channel="telegram:123"):
            with (
                patch("openjarvis.tools.notify_windows._send_toast"),
                patch(
                    "openjarvis.tools.notify_windows._send_to_channel",
                    side_effect=RuntimeError("telegram down"),
                ),
            ):
                assert deliver("t", "m") == ["desktop"]

    def test_raises_only_when_every_destination_fails(self):
        from openjarvis.tools.notify_windows import deliver

        with self._config(channel="telegram:123"):
            with (
                patch(
                    "openjarvis.tools.notify_windows._send_toast",
                    side_effect=RuntimeError("no toast"),
                ),
                patch(
                    "openjarvis.tools.notify_windows._send_to_channel",
                    side_effect=RuntimeError("telegram down"),
                ),
            ):
                try:
                    deliver("t", "m")
                except RuntimeError:
                    return
        raise AssertionError("expected a raise when nothing could be delivered")

    def test_desktop_can_be_turned_off(self):
        from openjarvis.tools.notify_windows import deliver

        with self._config(channel="telegram:123", desktop=False):
            with (
                patch("openjarvis.tools.notify_windows._send_toast") as toast,
                patch(
                    "openjarvis.tools.notify_windows._send_to_channel",
                    return_value=True,
                ),
            ):
                assert deliver("t", "m") == ["channel"]
        toast.assert_not_called()


class TestSpokenReminderIsFlattened:
    """A reminder must not reach a synthesiser as raw text.

    ``speak`` sends the reminder to Cartesia, so the text leaves the machine.
    Unflattened, a verification code or booking reference is uploaded to a
    third party verbatim and then read out loud in full. The toast still
    carries the original, so nothing is lost -- only unsaid.
    """

    def _spoken(self, text):
        from openjarvis.tools import notify_windows

        seen = {}

        def _fake_voice_wav(value):
            seen["text"] = value
            return None  # force the builtin fallback too

        def _fake_builtin(value):
            seen["builtin"] = value
            return True

        with patch.object(notify_windows, "_voice_wav", _fake_voice_wav):
            with patch.object(notify_windows, "_speak_builtin", _fake_builtin):
                with patch.object(notify_windows, "do_not_disturb", lambda: False):
                    notify_windows.speak(text)
        return seen

    def test_an_authentication_code_never_reaches_the_cloud_voice(self):
        seen = self._spoken("Your verification code is 483920")
        assert "483920" not in seen["text"]
        assert "the authentication code" in seen["text"]

    def test_the_builtin_fallback_gets_the_same_flattened_text(self):
        """Local playback still reads a code aloud in a room."""
        seen = self._spoken("Your verification code is 483920")
        assert "483920" not in seen["builtin"]
        assert seen["builtin"] == seen["text"]

    def test_a_long_reference_is_replaced(self):
        seen = self._spoken("Case Study due. Ref BN-20260902-69185569")
        assert "69185569" not in seen["text"]

    def test_ordinary_wording_is_left_alone(self):
        """Flattening must not mangle a normal reminder."""
        seen = self._spoken("Class starts in 10 minutes")
        assert "Class starts in 10 minutes" in seen["text"]
