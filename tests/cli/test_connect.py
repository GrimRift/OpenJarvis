"""Tests for ``jarvis connect`` CLI command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from openjarvis.cli import cli


def test_connect_list_no_connectors() -> None:
    """--list with an empty registry shows a 'no connectors' message."""
    runner = CliRunner()
    with mock.patch(
        "openjarvis.cli.connect_cmd.connect.__wrapped__"
        if hasattr(cli, "__wrapped__")
        else "openjarvis.core.registry.ConnectorRegistry.items",
        return_value=(),
    ):
        with mock.patch(
            "openjarvis.core.registry.ConnectorRegistry.items",
            return_value=(),
        ):
            result = runner.invoke(cli, ["connect", "--list"])

    assert result.exit_code == 0
    assert "No connectors registered" in result.output


def test_connect_list_with_connector(tmp_path: object) -> None:
    """--list with a connector registered shows it in the table."""
    runner = CliRunner()

    # Build a minimal mock connector class
    mock_cls = mock.MagicMock()
    mock_cls.auth_type = "filesystem"
    mock_instance = mock.MagicMock()
    mock_instance.is_connected.return_value = True
    mock_cls.return_value = mock_instance

    with mock.patch(
        "openjarvis.core.registry.ConnectorRegistry.items",
        return_value=(("obsidian", mock_cls),),
    ):
        result = runner.invoke(cli, ["connect", "--list"])

    assert result.exit_code == 0
    assert "obsidian" in result.output


def test_connect_help() -> None:
    """--help exits 0 and mentions the word 'connect'."""
    runner = CliRunner()
    result = runner.invoke(cli, ["connect", "--help"])
    assert result.exit_code == 0
    assert "connect" in result.output.lower()


def test_connect_specific_source(tmp_path: object) -> None:
    """connect --path /nonexistent obsidian shows an error gracefully."""
    runner = CliRunner()

    mock_cls = mock.MagicMock()
    mock_cls.auth_type = "filesystem"
    mock_instance = mock.MagicMock()
    # Path does not exist -> is_connected returns False
    mock_instance.is_connected.return_value = False
    mock_cls.return_value = mock_instance

    with (
        mock.patch(
            "openjarvis.core.registry.ConnectorRegistry.contains",
            return_value=True,
        ),
        mock.patch(
            "openjarvis.core.registry.ConnectorRegistry.get",
            return_value=mock_cls,
        ),
    ):
        # --path before the positional source arg (standard Click group behaviour)
        result = runner.invoke(cli, ["connect", "--path", "/nonexistent", "obsidian"])

    assert result.exit_code == 0
    # Should mention the source and give an indication something went wrong
    assert "obsidian" in result.output or "nonexistent" in result.output


def test_connect_filesystem_success_ingests_and_persists_state(
    tmp_path: Path,
) -> None:
    """A successful filesystem connect ingests into KnowledgeStore and saves state."""
    runner = CliRunner()

    mock_cls = mock.MagicMock()
    mock_cls.auth_type = "filesystem"
    mock_instance = mock.MagicMock()
    mock_instance.is_connected.return_value = True
    mock_cls.return_value = mock_instance

    mock_sync_engine = mock.MagicMock()
    mock_sync_engine.sync.return_value = 7

    with (
        mock.patch(
            "openjarvis.core.registry.ConnectorRegistry.contains",
            return_value=True,
        ),
        mock.patch(
            "openjarvis.core.registry.ConnectorRegistry.get",
            return_value=mock_cls,
        ),
        mock.patch("openjarvis.core.config.DEFAULT_CONFIG_DIR", tmp_path),
        mock.patch("openjarvis.connectors.store.KnowledgeStore"),
        mock.patch("openjarvis.connectors.pipeline.IngestionPipeline"),
        mock.patch(
            "openjarvis.connectors.sync_engine.SyncEngine",
            return_value=mock_sync_engine,
        ),
    ):
        result = runner.invoke(
            cli, ["connect", "--path", str(tmp_path / "vault"), "obsidian"]
        )

    assert result.exit_code == 0
    assert "indexed 7 chunks" in result.output
    mock_sync_engine.sync.assert_called_once_with(mock_instance)

    state_file = tmp_path / "connectors" / "obsidian.json"
    assert state_file.exists()
    assert json.loads(state_file.read_text()) == {"path": str(tmp_path / "vault")}


def test_connect_list_reflects_persisted_filesystem_connection(
    tmp_path: Path,
) -> None:
    """--list uses persisted state to correctly show a filesystem source connected."""
    runner = CliRunner()

    state_dir = tmp_path / "connectors"
    state_dir.mkdir()
    (state_dir / "obsidian.json").write_text(json.dumps({"path": "/my/vault"}))

    def _make_instance(vault_path: str = "") -> mock.MagicMock:
        instance = mock.MagicMock()
        instance.is_connected.return_value = bool(vault_path)
        return instance

    mock_cls = mock.MagicMock()
    mock_cls.auth_type = "filesystem"
    mock_cls.side_effect = _make_instance

    with (
        mock.patch(
            "openjarvis.core.registry.ConnectorRegistry.items",
            return_value=(("obsidian", mock_cls),),
        ),
        mock.patch("openjarvis.core.config.DEFAULT_CONFIG_DIR", tmp_path),
    ):
        result = runner.invoke(cli, ["connect", "--list"])

    assert result.exit_code == 0
    assert "connected" in result.output
    mock_cls.assert_called_once_with(vault_path="/my/vault")


def test_connect_oauth_authorizes_and_ingests() -> None:
    """A first OAuth connection immediately indexes connector documents."""
    runner = CliRunner()

    mock_cls = mock.MagicMock()
    mock_cls.auth_type = "oauth"
    disconnected = mock.MagicMock()
    disconnected.is_connected.return_value = False
    connected = mock.MagicMock()
    connected.is_connected.return_value = True
    mock_cls.side_effect = [disconnected, connected]

    provider = mock.MagicMock()
    sync_engine = mock.MagicMock()
    sync_engine.sync.return_value = 4

    with (
        mock.patch(
            "openjarvis.core.registry.ConnectorRegistry.contains",
            return_value=True,
        ),
        mock.patch(
            "openjarvis.core.registry.ConnectorRegistry.get",
            return_value=mock_cls,
        ),
        mock.patch(
            "openjarvis.connectors.oauth.get_provider_for_connector",
            return_value=provider,
        ),
        mock.patch(
            "openjarvis.connectors.oauth.get_client_credentials",
            return_value=("client-id", "client-secret"),
        ),
        mock.patch("openjarvis.connectors.oauth.run_connector_oauth") as oauth,
        mock.patch("openjarvis.connectors.store.KnowledgeStore"),
        mock.patch("openjarvis.connectors.pipeline.IngestionPipeline"),
        mock.patch(
            "openjarvis.connectors.sync_engine.SyncEngine",
            return_value=sync_engine,
        ),
    ):
        result = runner.invoke(cli, ["connect", "gmail"])

    assert result.exit_code == 0
    assert "gmail authorised — indexed 4 chunks" in result.output
    oauth.assert_called_once_with("gmail", "client-id", "client-secret")
    sync_engine.sync.assert_called_once_with(connected)


def test_connect_oauth_already_connected_runs_incremental_sync() -> None:
    """Reconnecting an authorized OAuth source performs an incremental sync."""
    runner = CliRunner()

    mock_cls = mock.MagicMock()
    mock_cls.auth_type = "oauth"
    connected = mock.MagicMock()
    connected.is_connected.return_value = True
    mock_cls.return_value = connected

    sync_engine = mock.MagicMock()
    sync_engine.sync.return_value = 2

    with (
        mock.patch(
            "openjarvis.core.registry.ConnectorRegistry.contains",
            return_value=True,
        ),
        mock.patch(
            "openjarvis.core.registry.ConnectorRegistry.get",
            return_value=mock_cls,
        ),
        mock.patch("openjarvis.connectors.oauth.run_connector_oauth") as oauth,
        mock.patch("openjarvis.connectors.store.KnowledgeStore"),
        mock.patch("openjarvis.connectors.pipeline.IngestionPipeline"),
        mock.patch(
            "openjarvis.connectors.sync_engine.SyncEngine",
            return_value=sync_engine,
        ),
    ):
        result = runner.invoke(cli, ["connect", "gmail"])

    assert result.exit_code == 0
    assert "gmail connected — indexed 2 chunks" in result.output
    oauth.assert_not_called()
    sync_engine.sync.assert_called_once_with(connected)


def test_connect_disconnect() -> None:
    """--disconnect gmail exits 0."""
    runner = CliRunner()

    mock_cls = mock.MagicMock()
    mock_instance = mock.MagicMock()
    mock_cls.return_value = mock_instance

    with (
        mock.patch(
            "openjarvis.core.registry.ConnectorRegistry.contains",
            return_value=True,
        ),
        mock.patch(
            "openjarvis.core.registry.ConnectorRegistry.get",
            return_value=mock_cls,
        ),
    ):
        result = runner.invoke(cli, ["connect", "--disconnect", "gmail"])

    assert result.exit_code == 0
    mock_instance.disconnect.assert_called_once()
