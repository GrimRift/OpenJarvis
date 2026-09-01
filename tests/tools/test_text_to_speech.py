"""Tests for the text_to_speech tool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from openjarvis.core.registry import ToolRegistry
from openjarvis.speech.tts import TTSResult


def test_tts_tool_registered():
    from openjarvis.tools.text_to_speech import TextToSpeechTool

    if not ToolRegistry.contains("text_to_speech"):
        ToolRegistry.register_value("text_to_speech", TextToSpeechTool)
    assert ToolRegistry.contains("text_to_speech")


def test_tts_tool_execute(tmp_path):
    from openjarvis.tools.text_to_speech import TextToSpeechTool

    tool = TextToSpeechTool()
    mock_result = TTSResult(
        audio=b"fake-audio-data",
        format="mp3",
        voice_id="jarvis",
        duration_seconds=2.5,
    )

    with patch("openjarvis.tools.text_to_speech.TTSRegistry") as mock_registry:
        mock_backend_cls = MagicMock()
        mock_backend_cls.return_value.synthesize.return_value = mock_result
        mock_registry.contains.return_value = True
        mock_registry.get.return_value = mock_backend_cls

        result = tool.execute(
            text="Good morning sir.",
            voice_id="jarvis",
            backend="cartesia",
            speed=0.9,
            volume=1.9,
            emotion="content",
            output_dir=str(tmp_path),
        )

    assert result.success is True
    assert "digest.mp3" in result.content
    assert (tmp_path / "digest.mp3").exists()
    assert (tmp_path / "digest.mp3").read_bytes() == b"fake-audio-data"
    mock_backend_cls.return_value.synthesize.assert_called_once_with(
        "Good morning sir.",
        voice_id="jarvis",
        speed=0.9,
        volume=1.9,
        emotion="content",
    )


def test_tts_tool_empty_text():
    from openjarvis.tools.text_to_speech import TextToSpeechTool

    tool = TextToSpeechTool()
    result = tool.execute(text="")
    assert result.success is False


def test_tts_tool_sanitizes_only_the_text_sent_to_the_backend(tmp_path):
    from openjarvis.tools.text_to_speech import TextToSpeechTool

    original = (
        "Open https://example.com/private and use "
        r"C:\AI\OpenJarvis-Lab\config.toml."
    )
    mock_result = TTSResult(
        audio=b"audio",
        format="mp3",
        voice_id="jarvis",
        duration_seconds=1.0,
    )

    with patch("openjarvis.tools.text_to_speech.TTSRegistry") as mock_registry:
        mock_backend_cls = MagicMock()
        mock_backend_cls.return_value.synthesize.return_value = mock_result
        mock_registry.contains.return_value = True
        mock_registry.get.return_value = mock_backend_cls

        result = TextToSpeechTool().execute(
            text=original,
            backend="cartesia",
            output_dir=str(tmp_path),
        )

    spoken_text = mock_backend_cls.return_value.synthesize.call_args.args[0]
    assert result.success is True
    assert original == (
        "Open https://example.com/private and use "
        r"C:\AI\OpenJarvis-Lab\config.toml."
    )
    assert "https://example.com/private" not in spoken_text
    assert r"C:\AI\OpenJarvis-Lab\config.toml" not in spoken_text
    assert "a link" in spoken_text
    assert "a file path" in spoken_text
    # The notice is now type-specific. The generic "The exact values are
    # visible in chat." was deliberately dropped when spoken notices were
    # narrowed; test_spoken_text.py and test_spoken_text_stream.py were updated
    # then, and this third call site was missed — the same one-of-three shape
    # the architecture invariants exist to catch.
    assert "The link is in chat." in spoken_text
    assert "The file path is in chat." in spoken_text
