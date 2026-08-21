"""Tests for OutlookConnector — OAuth-authenticated Outlook Mail sync (Graph).

All Graph API calls are mocked; no network access is required.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

from openjarvis.connectors._stubs import Document
from openjarvis.core.registry import ConnectorRegistry

# ---------------------------------------------------------------------------
# Helpers — fake Graph API payloads
# ---------------------------------------------------------------------------

_MSG1 = {
    "id": "msg1",
    "conversationId": "conv1",
    "subject": "Q3 Planning",
    "from": {"emailAddress": {"address": "Alice@Example.com"}},
    "toRecipients": [{"emailAddress": {"address": "me@example.com"}}],
    "ccRecipients": [],
    "receivedDateTime": "2024-01-01T10:00:00Z",
    "bodyPreview": "Hello world preview",
    "body": {"contentType": "text", "content": "Hello world"},
    "webLink": "https://outlook.office.com/mail/msg1",
}

_MSG2 = {
    "id": "msg2",
    "conversationId": "conv2",
    "subject": "Re: Budget",
    "from": {"emailAddress": {"address": "bob@example.com"}},
    "toRecipients": [{"emailAddress": {"address": "me@example.com"}}],
    "ccRecipients": [{"emailAddress": {"address": "carol@example.com"}}],
    "receivedDateTime": "2024-01-02T12:00:00Z",
    "bodyPreview": "Budget reply preview",
    "body": {"contentType": "text", "content": "Budget reply"},
    "webLink": "https://outlook.office.com/mail/msg2",
}

_LIST_RESPONSE = {"value": [_MSG1, _MSG2]}  # no @odata.nextLink → single page


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def connector(tmp_path: Path):
    """OutlookConnector pointing at a tmp credentials path (no file yet)."""
    from openjarvis.connectors.outlook import OutlookConnector  # noqa: PLC0415

    creds_path = str(tmp_path / "outlook.json")
    return OutlookConnector(credentials_path=creds_path)


def _write_creds(connector) -> Path:
    creds_path = Path(connector._credentials_path)
    creds_path.write_text(json.dumps({"token": "fake-access-token"}), encoding="utf-8")
    return creds_path


# ---------------------------------------------------------------------------


def test_not_connected_without_credentials(connector) -> None:
    assert connector.is_connected() is False


def test_auth_type_is_oauth(connector) -> None:
    assert connector.auth_type == "oauth"


def test_connector_id_and_display_name(connector) -> None:
    assert connector.connector_id == "outlook"
    assert connector.display_name == "Outlook Mail"


def test_no_send_delete_move_methods_exist(connector) -> None:
    """Deliberately read-only: no write-capable methods on this class."""
    for forbidden in ("send", "delete_message", "move_message", "mark_read"):
        assert not hasattr(connector, forbidden)


@patch("openjarvis.connectors.outlook._outlook_api_list_messages")
def test_sync_yields_documents(mock_list, connector) -> None:
    """sync() yields one Document per message with correct fields."""
    _write_creds(connector)
    mock_list.return_value = _LIST_RESPONSE

    docs: List[Document] = list(connector.sync())

    assert len(docs) == 2

    doc1 = next(d for d in docs if d.doc_id == "outlook:msg1")
    assert doc1.source == "outlook"
    assert doc1.doc_type == "email"
    assert doc1.title == "Q3 Planning"
    assert doc1.author == "alice@example.com"  # lowercased
    assert doc1.content == "Hello world"
    assert doc1.thread_id == "conv1"
    assert "alice@example.com" in doc1.participants
    assert "me@example.com" in doc1.participants
    assert doc1.url == "https://outlook.office.com/mail/msg1"

    doc2 = next(d for d in docs if d.doc_id == "outlook:msg2")
    assert doc2.title == "Re: Budget"
    assert "carol@example.com" in doc2.participants

    mock_list.assert_called_once()


@patch("openjarvis.connectors.outlook._outlook_api_list_messages")
def test_sync_paginates_via_odata_next_link(mock_list, connector) -> None:
    """A response carrying @odata.nextLink triggers a second page fetch."""
    _write_creds(connector)
    page1 = {"value": [_MSG1], "@odata.nextLink": "https://graph.microsoft.com/next"}
    page2 = {"value": [_MSG2]}
    mock_list.side_effect = [page1, page2]

    docs = list(connector.sync())

    assert len(docs) == 2
    assert mock_list.call_count == 2
    # Second call must reuse the nextLink verbatim, not rebuild params.
    _, second_kwargs = mock_list.call_args_list[1]
    assert second_kwargs["next_link"] == "https://graph.microsoft.com/next"


@patch("openjarvis.connectors.outlook._outlook_api_list_messages")
def test_sync_passes_since_as_filter(mock_list, connector) -> None:
    """sync(since=...) is forwarded to the list call, not silently dropped."""
    _write_creds(connector)
    mock_list.return_value = {"value": []}

    since_dt = datetime(2024, 6, 1, tzinfo=timezone.utc)
    list(connector.sync(since=since_dt))

    _, kwargs = mock_list.call_args
    assert kwargs["since"] == since_dt


@patch("openjarvis.connectors.outlook._outlook_api_list_messages")
def test_initial_sync_uses_dynamic_window_when_no_since_or_cursor(
    mock_list, connector
) -> None:
    """With neither since nor cursor, the configured history window applies."""
    _write_creds(connector)
    mock_list.return_value = {"value": []}

    list(connector.sync())

    _, kwargs = mock_list.call_args
    assert kwargs["since"] is not None


@patch("openjarvis.connectors.outlook._outlook_api_list_messages")
def test_resuming_from_cursor_does_not_reapply_since(mock_list, connector) -> None:
    """Resuming with a cursor must not also inject a since filter."""
    _write_creds(connector)
    mock_list.return_value = {"value": []}

    list(connector.sync(cursor="https://graph.microsoft.com/resume"))

    _, kwargs = mock_list.call_args
    assert kwargs["next_link"] == "https://graph.microsoft.com/resume"
    assert kwargs["since"] is None


def test_disconnect(connector) -> None:
    creds_path = _write_creds(connector)
    assert connector.is_connected() is True

    connector.disconnect()

    assert not creds_path.exists()
    assert connector.is_connected() is False


def test_mcp_tools(connector) -> None:
    tools = connector.mcp_tools()
    names = {t.name for t in tools}
    assert "outlook_search_emails" in names


def test_registry() -> None:
    """OutlookConnector can be registered and retrieved via ConnectorRegistry."""
    from openjarvis.connectors.outlook import OutlookConnector  # noqa: PLC0415

    # The registry is cleared before each test by the autouse conftest
    # fixture, so we imperatively re-register here (same pattern as
    # test_gmail.py / test_obsidian.py).
    ConnectorRegistry.register_value("outlook", OutlookConnector)
    assert ConnectorRegistry.contains("outlook")
    cls = ConnectorRegistry.get("outlook")
    assert cls.connector_id == "outlook"
    assert cls.auth_type == "oauth"
