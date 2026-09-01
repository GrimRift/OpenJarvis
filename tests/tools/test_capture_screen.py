"""Screen capture that asks the vision model directly.

`inspect_window` reads text and is blind to pixels: "identify the character on
my second monitor" had no answer, because a poster names itself nowhere the
accessibility tree can see. This tool captures and asks, returning prose — a
tool result is text, and OpenAI does not accept images in a tool message.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openjarvis.tools.capture_screen import (
    DEFAULT_QUESTION,
    CaptureScreenTool,
    _resolve_vision_model,
)
from openjarvis.tools.desktop_monitors import Monitor

EXTERNAL = Monitor(
    index=2,
    device=r"\\.\DISPLAY2",
    name="",
    width=1920,
    height=1080,
    x=0,
    y=0,
    is_primary=True,
)
LAPTOP = Monitor(
    index=1,
    device=r"\\.\DISPLAY1",
    name="",
    width=1536,
    height=960,
    x=-1920,
    y=0,
    is_primary=False,
)


class _Config:
    class vision:
        model = ""
        local_model = "qwen3-vl:8b"
        max_edge = 640

    class intelligence:
        default_model = "gpt-5.6-luna"


@pytest.fixture()
def engine():
    stub = MagicMock()
    stub.generate.return_value = {"content": "A red car on a beach."}
    return stub


@pytest.fixture()
def tool(engine):
    return CaptureScreenTool(config=_Config(), engine=engine)


def _patched(windows=None):
    """Monitors and windows, without touching the real desktop."""
    return (
        patch(
            "openjarvis.tools.capture_screen.list_monitors",
            return_value=[EXTERNAL, LAPTOP],
        ),
        patch(
            "openjarvis.tools.desktop_awareness._visible_windows",
            return_value=windows or [],
        ),
    )


def _image(width=1920, height=1080):
    from PIL import Image

    return Image.new("RGB", (width, height), (10, 20, 30))


class TestItAnswersAboutTheScreen:
    def test_it_asks_the_vision_model_and_returns_prose(self, tool, engine):
        monitors, windows = _patched()
        with monitors, windows, patch.object(tool, "_grab", return_value=_image()):
            result = tool.execute(monitor=1, question="What car is shown?")

        assert result.success is True
        assert "A red car on a beach." in result.content
        assert engine.generate.call_count == 1

    def test_the_question_reaches_the_model(self, tool, engine):
        monitors, windows = _patched()
        with monitors, windows, patch.object(tool, "_grab", return_value=_image()):
            tool.execute(monitor=1, question="Which character is shown?")
        sent = engine.generate.call_args.args[0][0]
        assert sent.content == "Which character is shown?"
        assert sent.images and sent.images[0].startswith("data:image/png;base64,")

    def test_a_missing_question_still_works(self, tool, engine):
        monitors, windows = _patched()
        with monitors, windows, patch.object(tool, "_grab", return_value=_image()):
            tool.execute(monitor=1)
        assert engine.generate.call_args.args[0][0].content == DEFAULT_QUESTION

    def test_it_names_which_monitor_it_looked_at(self, tool):
        monitors, windows = _patched()
        with monitors, windows, patch.object(tool, "_grab", return_value=_image()):
            result = tool.execute(monitor=2)
        assert "monitor 2" in result.content
        assert result.metadata["monitor"] == 2

    def test_it_defaults_to_the_primary(self, tool):
        monitors, windows = _patched()
        with monitors, windows, patch.object(tool, "_grab", return_value=_image()):
            result = tool.execute()
        assert result.metadata["monitor"] == EXTERNAL.index


class TestCaptureIsBounded:
    def test_a_large_screen_is_downscaled(self, tool):
        """A full 1920x1080 PNG is megabytes of base64 and buys nothing."""
        monitors, windows = _patched()
        with monitors, windows, patch.object(tool, "_grab", return_value=_image()):
            result = tool.execute(monitor=2)
        assert max(result.metadata["capture_size"]) == _Config.vision.max_edge

    def test_a_small_screen_is_left_alone(self, tool):
        monitors, windows = _patched()
        with monitors, windows, patch.object(
            tool, "_grab", return_value=_image(320, 200)
        ):
            result = tool.execute(monitor=2)
        assert result.metadata["capture_size"] == [320, 200]


class TestItRefusesSensitiveScreens:
    def test_a_password_manager_on_that_monitor_blocks_the_capture(self, tool, engine):
        """A capture takes in whatever is open, not just what was asked about."""
        window = {
            "title": "1Password",
            "handle": 1,
            "x": 100,
            "y": 100,
            "width": 400,
            "height": 300,
            "foreground": True,
        }
        monitors, windows = _patched([window])
        with monitors, windows, patch.object(tool, "_grab", return_value=_image()):
            result = tool.execute(monitor=2)

        assert result.success is False
        assert result.metadata["redacted"] is True
        engine.generate.assert_not_called()

    def test_a_sensitive_window_on_the_other_monitor_does_not_block(self, tool, engine):
        window = {
            "title": "1Password",
            "handle": 1,
            "x": -1800,
            "y": 100,
            "width": 400,
            "height": 300,
            "foreground": False,
        }
        monitors, windows = _patched([window])
        with monitors, windows, patch.object(tool, "_grab", return_value=_image()):
            result = tool.execute(monitor=2)
        assert result.success is True
        assert engine.generate.call_count == 1


class TestFailures:
    def test_an_unknown_monitor_lists_the_real_ones(self, tool):
        monitors, windows = _patched()
        with monitors, windows:
            result = tool.execute(monitor=9)
        assert result.success is False
        assert "Available" in result.content

    def test_a_capture_failure_is_reported_not_raised(self, tool):
        monitors, windows = _patched()
        with monitors, windows, patch.object(
            tool, "_grab", side_effect=OSError("no display")
        ):
            result = tool.execute(monitor=2)
        assert result.success is False
        assert "no display" in result.content

    def test_a_vision_failure_says_the_capture_worked(self, tool, engine):
        """Otherwise it reads as a screenshot problem and sends you looking."""
        engine.generate.side_effect = RuntimeError("model unavailable")
        monitors, windows = _patched()
        with monitors, windows, patch.object(tool, "_grab", return_value=_image()):
            result = tool.execute(monitor=2)
        assert result.success is False
        assert "Captured the screen" in result.content

    def test_an_empty_answer_is_not_reported_as_success(self, tool, engine):
        engine.generate.return_value = {"content": "   "}
        monitors, windows = _patched()
        with monitors, windows, patch.object(tool, "_grab", return_value=_image()):
            result = tool.execute(monitor=2)
        assert result.success is False


class TestModelResolution:
    def test_an_explicit_setting_wins(self):
        class Cfg(_Config):
            class vision:
                model = "my-vision-model"
                local_model = "qwen3-vl:8b"
                max_edge = 640

        assert _resolve_vision_model(Cfg()) == "my-vision-model"

    def test_it_falls_back_to_local_without_a_cloud_key(self):
        class Cfg:
            class vision:
                model = ""
                local_model = "qwen3-vl:8b"
                max_edge = 640

            class intelligence:
                default_model = "qwen3.5:4b"

        with patch("openjarvis.server.cloud_router._load_keys", return_value={}):
            assert _resolve_vision_model(Cfg()) == "qwen3-vl:8b"


class TestEmptyAnswerFromABudgetCeiling:
    """A reasoning model can spend its whole budget thinking and write nothing.

    Measured live on a 1280px capture of a busy browser window: 600 tokens gave
    `finish_reason: length` and zero characters; 3000 gave a full answer using
    1523. The first passing test was luck — a simpler screen needed less
    thinking — so this is the failure that looked intermittent.
    """

    def test_it_retries_with_headroom_when_the_budget_ran_out(self, tool, engine):
        engine.generate.side_effect = [
            {"content": "", "finish_reason": "length"},
            {"content": "A red car on a beach.", "finish_reason": "stop"},
        ]
        monitors, windows = _patched()
        with monitors, windows, patch.object(tool, "_grab", return_value=_image()):
            result = tool.execute(monitor=2)

        assert result.success is True
        assert "A red car on a beach." in result.content
        assert engine.generate.call_count == 2
        first, second = engine.generate.call_args_list
        assert second.kwargs["max_tokens"] > first.kwargs["max_tokens"]

    def test_a_genuinely_empty_answer_is_not_retried(self, tool, engine):
        """`stop` with no text means nothing to say, not a truncated thought."""
        engine.generate.side_effect = [{"content": "", "finish_reason": "stop"}]
        monitors, windows = _patched()
        with monitors, windows, patch.object(tool, "_grab", return_value=_image()):
            result = tool.execute(monitor=2)

        assert result.success is False
        assert engine.generate.call_count == 1

    def test_the_retry_is_bounded_to_one(self, tool, engine):
        engine.generate.side_effect = [
            {"content": "", "finish_reason": "length"},
            {"content": "", "finish_reason": "length"},
        ]
        monitors, windows = _patched()
        with monitors, windows, patch.object(tool, "_grab", return_value=_image()):
            result = tool.execute(monitor=2)

        assert result.success is False
        assert engine.generate.call_count == 2
