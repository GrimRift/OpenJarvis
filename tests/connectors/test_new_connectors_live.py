"""Live smoke tests for new connectors — require real API credentials.

Run with: uv run pytest tests/connectors/test_new_connectors_live.py -v -m cloud
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from openjarvis.connectors._stubs import Document



def _connector_configured(name: str) -> bool:
    """Whether this machine has credentials for *name*.

    These are live tests: without the connector's own JSON they raise
    FileNotFoundError, which reads as a failure when it only means the
    account was never linked here. Note the Google Tasks case is NOT gated
    this way -- it is configured and returns 403, which is a real fault.
    """
    from openjarvis.core.paths import get_data_dir

    return (get_data_dir() / "connectors" / f"{name}.json").exists()


@pytest.mark.cloud
@pytest.mark.skipif(
    not _connector_configured("oura"), reason="Oura is not linked on this machine"
)
class TestOuraLive:
    def test_sync_returns_documents(self):
        from openjarvis.connectors.oura import OuraConnector

        conn = OuraConnector()  # Uses default token path
        docs = list(conn.sync(since=datetime.now() - timedelta(days=1)))
        assert len(docs) > 0
        assert all(isinstance(d, Document) for d in docs)
        assert all(d.source == "oura" for d in docs)


@pytest.mark.cloud
@pytest.mark.skipif(
    not _connector_configured("strava"),
    reason="Strava is not linked on this machine",
)
class TestStravaLive:
    def test_sync_returns_documents(self):
        from openjarvis.connectors.strava import StravaConnector

        conn = StravaConnector()
        docs = list(conn.sync(since=datetime.now() - timedelta(days=7)))
        assert all(isinstance(d, Document) for d in docs)
        assert all(d.source == "strava" for d in docs)


@pytest.mark.cloud
class TestSpotifyLive:
    def test_sync_returns_documents(self):
        from openjarvis.connectors.spotify import SpotifyConnector

        conn = SpotifyConnector()
        docs = list(conn.sync(since=datetime.now() - timedelta(days=1)))
        assert all(isinstance(d, Document) for d in docs)
        assert all(d.source == "spotify" for d in docs)


@pytest.mark.cloud
class TestGoogleTasksLive:
    def test_sync_returns_documents(self):
        from openjarvis.connectors.google_tasks import GoogleTasksConnector

        conn = GoogleTasksConnector()
        docs = list(conn.sync(since=datetime.now() - timedelta(days=7)))
        assert all(isinstance(d, Document) for d in docs)
        assert all(d.source == "google_tasks" for d in docs)
