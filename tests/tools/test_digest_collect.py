"""Tests for the digest_collect tool."""

from __future__ import annotations

from datetime import datetime
from threading import Barrier
from unittest.mock import MagicMock, patch

from openjarvis.connectors._stubs import Document
from openjarvis.core.registry import ConnectorRegistry, ToolRegistry


def test_digest_collect_registered():
    from openjarvis.tools.digest_collect import DigestCollectTool

    ToolRegistry.register_value("digest_collect", DigestCollectTool)
    assert ToolRegistry.contains("digest_collect")


def test_digest_collect_executes():
    from openjarvis.tools.digest_collect import DigestCollectTool

    tool = DigestCollectTool()

    mock_docs = [
        Document(
            doc_id="test-1",
            source="gmail",
            doc_type="email",
            content="Meeting at 3pm",
            title="Team standup",
            author="alice@example.com",
            timestamp=datetime(2026, 4, 1, 10, 0),
        )
    ]

    mock_connector = MagicMock()
    mock_connector.return_value.is_connected.return_value = True
    mock_connector.return_value.sync.return_value = mock_docs

    with patch.object(ConnectorRegistry, "contains", return_value=True):
        with patch.object(ConnectorRegistry, "get", return_value=mock_connector):
            result = tool.execute(sources=["gmail"], hours_back=24)

    assert result.success is True
    assert "=== MESSAGES ===" in result.content
    assert "[gmail id=test-1] From: alice@example.com" in result.content
    assert "Team standup" in result.content
    assert result.metadata["total_items"] == 1


def test_digest_collect_executes_obsidian():
    """Recently-modified Obsidian notes surface under a NOTES section."""
    from openjarvis.tools.digest_collect import DigestCollectTool

    tool = DigestCollectTool()

    mock_docs = [
        Document(
            doc_id="obsidian:Class Schedule.md",
            source="obsidian",
            doc_type="note",
            content="# Class Schedule\n\nFriday classes...",
            title="Class Schedule",
            timestamp=datetime(2026, 8, 21, 5, 42),
        )
    ]

    mock_connector = MagicMock()
    mock_connector.return_value.is_connected.return_value = True
    mock_connector.return_value.sync.return_value = mock_docs

    with patch.object(ConnectorRegistry, "contains", return_value=True):
        with patch.object(ConnectorRegistry, "get", return_value=mock_connector):
            result = tool.execute(sources=["obsidian"], hours_back=48)

    assert result.success is True
    assert "=== NOTES ===" in result.content
    assert "[notes] Class Schedule" in result.content


def test_digest_collect_missing_connector():
    from openjarvis.tools.digest_collect import DigestCollectTool

    tool = DigestCollectTool()

    with patch.object(ConnectorRegistry, "contains", return_value=False):
        result = tool.execute(sources=["nonexistent"])

    assert result.success is True  # Partial success
    assert "not available" in result.content


def test_digest_collect_fetches_sources_concurrently():
    """Independent connector waits must overlap instead of adding latency."""
    from openjarvis.tools.digest_collect import DigestCollectTool

    rendezvous = Barrier(2, timeout=2)

    class GmailConnector:
        auth_type = "oauth"

        def is_connected(self):
            return True

        def sync(self, **kwargs):
            rendezvous.wait()
            return [
                Document(
                    doc_id="mail-1",
                    source="gmail",
                    doc_type="email",
                    content="Hello",
                    title="Inbox item",
                    timestamp=datetime.now(),
                )
            ]

    class CalendarConnector:
        auth_type = "oauth"

        def is_connected(self):
            return True

        def sync(self, **kwargs):
            rendezvous.wait()
            return [
                Document(
                    doc_id="event-1",
                    source="gcalendar",
                    doc_type="event",
                    content="",
                    title="Calendar item",
                    timestamp=datetime.now(),
                )
            ]

    connector_classes = {
        "gmail": GmailConnector,
        "gcalendar": CalendarConnector,
    }
    with patch.object(ConnectorRegistry, "contains", return_value=True):
        with patch.object(
            ConnectorRegistry,
            "get",
            side_effect=lambda source: connector_classes[source],
        ):
            result = DigestCollectTool().execute(
                sources=["gmail", "gcalendar"], hours_back=24
            )

    assert result.success is True
    assert result.metadata["sources_ok"] == ["gmail", "gcalendar"]
    assert result.metadata["sources_failed"] == []
