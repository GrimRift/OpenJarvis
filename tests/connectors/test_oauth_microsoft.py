"""Tests for the Microsoft OAuth provider registration and consent fan-out.

Mirrors the "one consent authorises every connector under this provider"
behavior Google already relies on (oauth.py's credential_files fan-out).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from openjarvis.connectors.oauth import (
    OAUTH_PROVIDERS,
    get_provider_for_connector,
)


def test_microsoft_provider_registered() -> None:
    assert "microsoft" in OAUTH_PROVIDERS
    provider = OAUTH_PROVIDERS["microsoft"]
    assert provider.display_name == "Microsoft"
    assert "Mail.Read" in provider.scopes
    assert "offline_access" in provider.scopes


def test_outlook_resolves_to_microsoft_provider() -> None:
    provider = get_provider_for_connector("outlook")
    assert provider is not None
    assert provider.name == "microsoft"


def test_run_connector_oauth_writes_tokens_to_all_credential_files(
    tmp_path: Path,
) -> None:
    """One consent flow persists the same token payload to every credential
    file the provider covers (today: microsoft.json + outlook.json) — same
    fan-out behavior the Google provider already relies on."""
    from openjarvis.connectors import oauth as oauth_mod

    with (
        patch.object(oauth_mod, "_CONNECTORS_DIR", tmp_path),
        patch.object(oauth_mod, "open_browser"),
        patch.object(oauth_mod, "_wait_for_callback_code", return_value="auth-code"),
        patch.object(
            oauth_mod,
            "_exchange_token",
            return_value={
                "access_token": "graph-access-token",
                "refresh_token": "graph-refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        ),
    ):
        oauth_mod.run_connector_oauth("outlook", "client-id", "client-secret")

    for filename in ("microsoft.json", "outlook.json"):
        payload = json.loads((tmp_path / filename).read_text())
        assert payload["access_token"] == "graph-access-token"
        assert payload["refresh_token"] == "graph-refresh-token"
        assert payload["client_id"] == "client-id"
        assert payload["client_secret"] == "client-secret"
