"""Tests for small cross-platform core utilities."""

from __future__ import annotations

from unittest.mock import patch

from openjarvis.core import utils


def test_open_browser_windows_preserves_complete_oauth_query() -> None:
    """Windows passes every OAuth query parameter without command-shell parsing."""
    url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        "?client_id=test.apps.googleusercontent.com"
        "&redirect_uri=http%3A%2F%2F127.0.0.1%3A8789%2Fcallback"
        "&response_type=code"
        "&scope=openid+email"
        "&access_type=offline"
    )

    with (
        patch("openjarvis.core.utils.platform.system", return_value="Windows"),
        patch("openjarvis.core.utils.os.startfile", create=True) as startfile,
        patch("openjarvis.core.utils.webbrowser.open") as fallback,
    ):
        utils.open_browser(url)

    startfile.assert_called_once_with(url)
    fallback.assert_not_called()
