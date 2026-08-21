"""Tests for microsoft_auth.py — token read + one-shot 401 refresh.

Mirrors tests/connectors/test_gmail.py's refresh-test section, against the
Microsoft token endpoint instead of Google's.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


class _FakeResponse:
    """Minimal stand-in for httpx.Response used by the refresh test."""

    def __init__(
        self, *, status_code: int, json_data: dict | None = None, text: str = ""
    ):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx as _httpx

            raise _httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self
            )


def _write_full_creds(tmp_path: Path) -> str:
    creds_path = tmp_path / "outlook.json"
    creds_path.write_text(
        json.dumps(
            {
                "access_token": "old-access-token",
                "refresh_token": "stored-refresh-token",
                "client_id": "client-id-abc",
                "client_secret": "client-secret-xyz",
            }
        ),
        encoding="utf-8",
    )
    return str(creds_path)


def test_current_access_token_empty_without_credentials(tmp_path: Path) -> None:
    from openjarvis.connectors import microsoft_auth

    assert microsoft_auth.current_access_token(str(tmp_path / "missing.json")) == ""


def test_current_access_token_reads_stored_token(tmp_path: Path) -> None:
    from openjarvis.connectors import microsoft_auth

    creds_path = _write_full_creds(tmp_path)
    assert microsoft_auth.current_access_token(creds_path) == "old-access-token"


def test_401_triggers_refresh_and_retries_with_new_token(tmp_path: Path) -> None:
    """A 401 on an Outlook API call refreshes the token, persists it, retries."""
    from openjarvis.connectors import microsoft_auth as ms_auth

    creds_path = _write_full_creds(tmp_path)

    get_calls: list[dict] = []

    def fake_api_fn(token: str, msg_id: str) -> dict:
        get_calls.append({"token": token, "msg_id": msg_id})
        if len(get_calls) == 1:
            import httpx as _httpx

            resp = _FakeResponse(status_code=401, text="unauthorized")
            raise _httpx.HTTPStatusError("HTTP 401", request=None, response=resp)
        return {"id": msg_id}

    post_calls: list[dict] = []

    def fake_post(url, *, data, timeout):
        post_calls.append({"url": url, "data": dict(data)})
        return _FakeResponse(
            status_code=200,
            json_data={"access_token": "fresh-access-token", "expires_in": 3599},
        )

    with patch.object(ms_auth.httpx, "post", side_effect=fake_post):
        result = ms_auth.call_with_refresh(fake_api_fn, creds_path, "msg-1")

    assert len(get_calls) == 2
    assert get_calls[0]["token"] == "old-access-token"
    assert get_calls[1]["token"] == "fresh-access-token"

    assert len(post_calls) == 1
    assert post_calls[0]["url"] == (
        "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    )
    assert post_calls[0]["data"] == {
        "client_id": "client-id-abc",
        "client_secret": "client-secret-xyz",
        "refresh_token": "stored-refresh-token",
        "grant_type": "refresh_token",
    }

    on_disk = json.loads(Path(creds_path).read_text(encoding="utf-8"))
    assert on_disk["access_token"] == "fresh-access-token"
    assert on_disk["token"] == "fresh-access-token"
    assert on_disk["expires_in"] == 3599

    assert result == {"id": "msg-1"}


def test_non_401_status_is_not_refreshed(tmp_path: Path) -> None:
    """A non-401 error must propagate — only 401 triggers refresh."""
    import httpx as _httpx

    from openjarvis.connectors import microsoft_auth as ms_auth

    creds_path = _write_full_creds(tmp_path)

    def fake_api_fn(token: str) -> dict:
        resp = _FakeResponse(status_code=503, text="service unavailable")
        raise _httpx.HTTPStatusError("HTTP 503", request=None, response=resp)

    with patch.object(
        ms_auth.httpx,
        "post",
        side_effect=AssertionError("must not refresh on non-401 status"),
    ):
        with pytest.raises(_httpx.HTTPStatusError):
            ms_auth.call_with_refresh(fake_api_fn, creds_path)


def test_refresh_raises_when_refresh_token_missing(tmp_path: Path) -> None:
    from openjarvis.connectors import microsoft_auth as ms_auth

    creds_path = tmp_path / "outlook.json"
    creds_path.write_text(
        json.dumps({"access_token": "stale", "client_id": "c", "client_secret": "s"}),
        encoding="utf-8",
    )

    with pytest.raises(ms_auth.MicrosoftAuthError, match="refresh_token"):
        ms_auth.refresh_access_token(str(creds_path))


def test_refresh_raises_when_no_credentials_file(tmp_path: Path) -> None:
    from openjarvis.connectors import microsoft_auth as ms_auth

    with pytest.raises(ms_auth.MicrosoftAuthError):
        ms_auth.refresh_access_token(str(tmp_path / "missing.json"))


def test_refresh_persists_rotated_refresh_token(tmp_path: Path) -> None:
    """Microsoft may rotate the refresh_token on use; the new one must be saved."""
    from openjarvis.connectors import microsoft_auth as ms_auth

    creds_path = _write_full_creds(tmp_path)

    def fake_post(url, *, data, timeout):
        return _FakeResponse(
            status_code=200,
            json_data={
                "access_token": "fresh-access-token",
                "refresh_token": "rotated-refresh-token",
                "expires_in": 3599,
            },
        )

    with patch.object(ms_auth.httpx, "post", side_effect=fake_post):
        ms_auth.refresh_access_token(creds_path)

    on_disk = json.loads(Path(creds_path).read_text(encoding="utf-8"))
    assert on_disk["refresh_token"] == "rotated-refresh-token"


def test_refresh_raises_on_non_200_from_token_endpoint(tmp_path: Path) -> None:
    from openjarvis.connectors import microsoft_auth as ms_auth

    creds_path = _write_full_creds(tmp_path)

    def fake_post(url, *, data, timeout):
        return _FakeResponse(status_code=400, text="invalid_grant")

    with patch.object(ms_auth.httpx, "post", side_effect=fake_post):
        with pytest.raises(ms_auth.MicrosoftAuthError, match="400"):
            ms_auth.refresh_access_token(creds_path)
