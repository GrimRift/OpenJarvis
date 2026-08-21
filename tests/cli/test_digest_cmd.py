"""Tests for `jarvis digest` CLI command."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from click.testing import CliRunner

from openjarvis.agents.digest_store import DigestArtifact, DigestStore


def test_digest_command_exists():
    """The digest command is registered on the CLI."""
    from openjarvis.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["digest", "--help"])
    assert result.exit_code == 0
    assert "digest" in result.output.lower()


def test_digest_displays_cached(tmp_path):
    from openjarvis.cli import cli

    db_path = str(tmp_path / "digest.db")
    store = DigestStore(db_path=db_path)
    store.save(
        DigestArtifact(
            text="# Messages\nYou have 3 emails.\n# Calendar\n2 meetings today.",
            audio_path=Path("/nonexistent/audio.mp3"),
            sections={},
            sources_used=["gmail"],
            generated_at=datetime.now(tz=__import__("datetime").timezone.utc),
            model_used="test",
            voice_used="jarvis",
        )
    )
    store.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["digest", "--text-only", "--db-path", db_path])
    assert result.exit_code == 0
    assert "3 emails" in result.output


def test_digest_displays_cached_no_audio_path_does_not_attempt_playback(tmp_path):
    """audio_path=None (TTS failed/unconfigured) must not trigger a
    playback attempt — regression for the old Path("") false-positive."""
    from unittest.mock import patch

    from openjarvis.cli import cli

    db_path = str(tmp_path / "digest.db")
    store = DigestStore(db_path=db_path)
    store.save(
        DigestArtifact(
            text="Text-only digest.",
            audio_path=None,
            sections={},
            sources_used=["gmail"],
            generated_at=datetime.now(tz=__import__("datetime").timezone.utc),
            model_used="test",
            voice_used="",
        )
    )
    store.close()

    runner = CliRunner()
    with patch("openjarvis.cli.digest_cmd._play_audio") as mock_play:
        result = runner.invoke(cli, ["digest", "--db-path", db_path])

    assert result.exit_code == 0
    mock_play.assert_not_called()


def test_digest_fresh_text_only_does_not_play_audio(tmp_path):
    """--fresh --text-only must not attempt playback or misreport audio as
    available when no TTS backend produced one."""
    from unittest.mock import MagicMock, patch

    from openjarvis.cli import cli

    db_path = str(tmp_path / "digest.db")

    def fake_ask(*args, **kwargs):
        store = DigestStore(db_path=db_path)
        store.save(
            DigestArtifact(
                text="Freshly generated digest.",
                audio_path=None,
                sections={},
                sources_used=["gmail"],
                generated_at=datetime.now(tz=__import__("datetime").timezone.utc),
                model_used="test",
                voice_used="",
            )
        )
        store.close()
        return "Freshly generated digest."

    mock_jarvis = MagicMock()
    mock_jarvis.__enter__.return_value = mock_jarvis
    mock_jarvis.__exit__.return_value = False
    mock_jarvis.ask.side_effect = fake_ask

    runner = CliRunner()
    with (
        patch("openjarvis.sdk.Jarvis", return_value=mock_jarvis),
        patch("openjarvis.cli.digest_cmd._play_audio") as mock_play,
    ):
        result = runner.invoke(
            cli, ["digest", "--fresh", "--text-only", "--db-path", db_path]
        )

    assert result.exit_code == 0
    assert "Playing audio" not in result.output
    assert "Audio available: False" in result.output
    mock_play.assert_not_called()


def test_digest_no_cache(tmp_path):
    from openjarvis.cli import cli

    db_path = str(tmp_path / "empty.db")
    runner = CliRunner()
    result = runner.invoke(cli, ["digest", "--db-path", db_path])
    assert result.exit_code == 0
    assert "No digest for today" in result.output
