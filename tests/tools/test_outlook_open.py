"""outlook_open finds an Outlook email, opens it and names it (2 October).

"Show me the PMFC examination reminder" had nothing to open it with: the
email is in Outlook and only gmail_open existed, which opened an unrelated
Gmail promo instead.
"""

from __future__ import annotations

import contextlib
import json

import pytest

from openjarvis.tools import opera_control
from openjarvis.tools.opera_control import OutlookOpenTool

ROWS = {
    "Focused": [
        {"label": "NU Information Security Daily Digest Thu 11:31 AM", "when": ""},
    ],
    "Other": [
        {
            "label": "Ask Lex PH Academy [EXT]PMFC83 Examination Reminder Tue",
            "when": "Tue 9/29/2026 8:02 PM",
        },
    ],
}


class _Inbox:
    def __init__(self):
        self.tab = "Focused"
        self.clicked = []
        self.visited = []

    def navigate(self, url, timeout=0):
        self.visited.append(url)

    def wait_for(self, expression, timeout=0):
        return True

    def evaluate(self, expression):
        if "const words" in expression:
            words = json.loads(expression.split("const words = ", 1)[1].split(";")[0])
            best, best_score = None, 0
            for index, row in enumerate(ROWS[self.tab]):
                score = sum(1 for w in words if w in row["label"].lower())
                if score > best_score:
                    best, best_score = index, score
            if best is None:
                return None
            row = ROWS[self.tab][best]
            return {
                "index": best,
                "score": best_score,
                "label": row["label"],
                "when": row["when"],
            }
        if "row.click()" in expression:
            index = int(expression.rsplit(")[", 1)[1].split("]")[0])
            self.clicked.append((self.tab, index))
            return True
        return None


@pytest.fixture
def inbox(monkeypatch):
    page = _Inbox()

    @contextlib.contextmanager
    def _session(own_window=False, transient=False):
        yield type("S", (), {"page": page})()

    def _select(p, label):
        p.tab = label
        return True

    monkeypatch.setattr(opera_control, "opera_session", _session)
    monkeypatch.setattr(opera_control, "_select_inbox_tab", _select)
    monkeypatch.setattr(opera_control, "_rows_after_switch", lambda p, n: ([], None))
    monkeypatch.setattr(OutlookOpenTool, "_guard", lambda self, minimized=False: None)
    return page


def test_it_opens_the_matching_email_from_the_tab_it_is_in(inbox):
    result = OutlookOpenTool().execute(query="PMFC examination reminder")
    assert result.success and result.metadata["found"] is True
    assert inbox.clicked == [("Other", 0)]
    assert "PMFC83 Examination Reminder" in result.content
    assert "received Tue 9/29/2026 8:02 PM" in result.content


def test_a_weak_match_is_not_opened(inbox):
    result = OutlookOpenTool().execute(query="Quilgo certificate download link")
    assert inbox.clicked == []
    assert "nothing was opened" in result.content
    assert result.metadata["found"] is False
