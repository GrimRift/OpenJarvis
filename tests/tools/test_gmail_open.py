"""gmail_open names what it opened and refuses a weak match (2 October).

Asked to show the "PMFC examination reminder" -- an Outlook email -- it
opened an Academia.edu promo that shared only the word "reminder", three
times, and Sage said "It's open in Gmail" each time.
"""

from __future__ import annotations

import contextlib

import pytest

from openjarvis.tools import gmail_open as gmail_open_module
from openjarvis.tools.gmail_open import GmailOpenTool


def _message(subject, sender, snippet="", date="Fri, 28 Aug 2026 12:10:00 +0800"):
    return {
        "id": "m1",
        "snippet": snippet,
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "Date", "value": date},
            ]
        },
    }


@pytest.fixture
def opened(monkeypatch, tmp_path):
    from openjarvis.connectors import google_auth
    from openjarvis.tools import gmail_read, opera_control

    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setattr(gmail_read, "_TOKEN_PATH", str(token))
    monkeypatch.setattr(opera_control, "ensure_opera", lambda minimized=False: None)
    monkeypatch.setattr(google_auth, "call_with_refresh", lambda fn, path: fn("t"))
    visited = []

    class _Page:
        def navigate(self, url, timeout=0):
            visited.append(url)

    @contextlib.contextmanager
    def _session(own_window=False, transient=False):
        yield type("S", (), {"page": _Page(), "move_to_monitor": lambda self, m: ""})()

    monkeypatch.setattr(opera_control, "opera_session", _session)

    def use(found):
        monkeypatch.setattr(
            gmail_read, "search_messages", lambda token, query, count: ["m1"]
        )
        monkeypatch.setattr(gmail_read, "_metadata_message", lambda token, mid: found)
        return visited

    return use


def test_a_weak_match_is_not_opened(opened):
    visited = opened(
        _message(
            "One last reminder: try Academia Premium for $1",
            "Academia.edu <premium@academia-mail.com>",
        )
    )
    result = GmailOpenTool().execute(query="PMFC examination reminder")
    assert visited == []
    assert "nothing was opened" in result.content
    assert "Academia Premium" in result.content
    assert "outlook_open" in result.content
    assert result.metadata["found"] is False


def test_a_good_match_is_opened_and_named(opened):
    visited = opened(
        _message(
            "PMFC Examination Reminder",
            "Ask Lex PH Academy <info@asklex.ph>",
        )
    )
    result = GmailOpenTool().execute(query="PMFC examination reminder")
    assert visited and visited[0].endswith("/m1")
    assert '"PMFC Examination Reminder" from Ask Lex PH Academy' in result.content
    assert result.metadata["found"] is True


def test_module_imports_cleanly():
    assert gmail_open_module.GmailOpenTool is GmailOpenTool
