"""Briefs remember what they said and what is done (2 October).

A finished PMFC exam and already-fixed CI failures led the morning briefs for
days. Now each brief is given its earlier briefs, the user's done list and
GitHub's latest CI result, with the user's rules for repeats.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from openjarvis.agents import brief_memory

_REAL_CI_STATUS = brief_memory.ci_status


@pytest.fixture(autouse=True)
def _no_github(monkeypatch):
    monkeypatch.setattr(brief_memory, "ci_status", lambda timeout=10.0: "")


def test_done_items_are_kept_listed_and_expire(tmp_path):
    brief_memory.mark_done("PMFC83 examination", tmp_path)
    brief_memory.mark_done("pmfc83 EXAMINATION", tmp_path)  # same item, once
    assert [i["item"] for i in brief_memory.done_items(tmp_path)] == [
        "pmfc83 EXAMINATION"
    ]
    old = (dt.date.today() - dt.timedelta(days=40)).isoformat()
    (tmp_path / "brief_done.json").write_text(
        json.dumps([{"item": "ancient", "day": old}]), encoding="utf-8"
    )
    assert brief_memory.done_items(tmp_path) == []


def test_the_block_carries_earlier_briefs_done_items_and_rules(tmp_path):
    brief_memory.mark_done("PMFC83 examination", tmp_path)
    yesterday = dt.date.today() - dt.timedelta(days=1)
    block = brief_memory.memory_block(
        [(yesterday, "School: PMFC exam, submit by tonight.")], tmp_path
    )
    assert f"{yesterday:%a %b} {yesterday.day}: School: PMFC exam" in block
    assert "DONE; leave them out" in block and "PMFC83 examination" in block
    assert "'Still open since <weekday>:'" in block
    assert "new items first" in block
    assert "A deadline whose date has passed is not 'tonight'" in block
    assert "GitHub CI status unknown" in block


def test_digest_briefs_skip_today_and_old_ones(tmp_path):
    today = dt.date.today()
    with sqlite3.connect(str(tmp_path / "digest.db")) as db:
        db.execute("create table digests (generated_at text, text text)")
        for days, text in ((5, "too old"), (1, "yesterday's"), (0, "today's")):
            stamp = (today - dt.timedelta(days=days)).isoformat() + "T05:00:00"
            db.execute("insert into digests values (?, ?)", (stamp, text))
    assert [t for _, t in brief_memory.digest_briefs(tmp_path)] == ["yesterday's"]


def test_operator_briefs_skip_failed_runs(tmp_path):
    yesterday = dt.datetime.combine(
        dt.date.today() - dt.timedelta(days=1), dt.time(6, 30)
    ).timestamp()
    with sqlite3.connect(str(tmp_path / "traces.db")) as db:
        db.execute("create table traces (agent text, started_at real, result text)")
        db.execute("insert into traces values ('op', ?, 'School: PMFC')", (yesterday,))
        db.execute(
            "insert into traces values ('op', ?, 'Maximum turns reached.')",
            (yesterday + 60,),
        )
        db.execute("insert into traces values ('other', ?, 'x')", (yesterday,))
    briefs = brief_memory.operator_briefs("op", tmp_path)
    assert [t for _, t in briefs] == ["School: PMFC"]


class TestCiStatus:
    @pytest.fixture(autouse=True)
    def _real(self, monkeypatch):
        # This class tests the real ci_status; only subprocess is faked.
        monkeypatch.setattr(brief_memory, "ci_status", _REAL_CI_STATUS)

    def _fake_run(self, monkeypatch, runs):
        def run(cmd, **kwargs):
            if cmd[0] == "git" and "rev-parse" in cmd:
                return SimpleNamespace(stdout="feature/sage-customization\n")
            if cmd[0] == "git":
                return SimpleNamespace(
                    stdout="https://github.com/GrimRift/OpenJarvis.git\n"
                )
            assert cmd[:5] == ["gh", "run", "list", "-R", "GrimRift/OpenJarvis"]
            return SimpleNamespace(stdout=json.dumps(runs))

        monkeypatch.setattr(brief_memory.subprocess, "run", run)

    def test_a_passing_latest_run_retires_old_failures(self, monkeypatch):
        self._fake_run(
            monkeypatch,
            [
                {"status": "completed", "conclusion": "cancelled"},
                {
                    "status": "completed",
                    "conclusion": "success",
                    "createdAt": "2026-10-01T20:38:54Z",
                },
                {"status": "completed", "conclusion": "failure"},
            ],
        )
        line = brief_memory.ci_status()
        assert "PASSED (2026-10-01 20:38 UTC)" in line
        assert "do not mention it" in line

    def test_a_failing_latest_run_is_mentioned_once(self, monkeypatch):
        self._fake_run(
            monkeypatch,
            [
                {
                    "status": "completed",
                    "conclusion": "failure",
                    "createdAt": "2026-10-02T01:00:00Z",
                    "displayTitle": "Break it",
                }
            ],
        )
        assert "FAILED" in brief_memory.ci_status()

    def test_no_github_means_no_claim(self, monkeypatch):
        def run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 10)

        monkeypatch.setattr(brief_memory.subprocess, "run", run)
        assert brief_memory.ci_status() == ""


def test_mark_done_tool(tmp_path, monkeypatch):
    from openjarvis.tools.brief_done import MarkDoneTool

    monkeypatch.setattr(brief_memory, "get_config_dir", lambda: tmp_path)
    result = MarkDoneTool().execute(item="PMFC83 examination")
    assert result.success and "Noted as done: PMFC83 examination" in result.content
    assert brief_memory.done_items(tmp_path)[0]["item"] == "PMFC83 examination"
    assert MarkDoneTool().execute(item="  ").success is False
