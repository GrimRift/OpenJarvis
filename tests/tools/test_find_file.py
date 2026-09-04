"""Finding a file the user has lost.

The failure that matters here is not "no results" — it is a search that
*reports* it looked everywhere when it did not. The budget tests exist because
the first version spent its entire allowance inside the priority folders and
never reached the drive walk at all, while still answering as though it had.
"""

from __future__ import annotations

import os
import time

import pytest

from openjarvis.tools import find_file
from openjarvis.tools.find_file import FindFileTool, _matcher, search


class TestMatching:
    def test_a_plain_query_is_a_substring(self):
        matches = _matcher("report", "")
        assert matches("Q3-report-final.docx") is True
        assert matches("summary.docx") is False

    def test_it_ignores_case(self):
        assert _matcher("REPORT", "")("my report.txt") is True

    def test_a_wildcard_query_is_treated_as_a_glob(self):
        """Someone who types '*.rvt' means it."""
        matches = _matcher("*.rvt", "")
        assert matches("tower.rvt") is True
        assert matches("tower.rvt.bak") is False

    def test_an_extension_narrows_without_a_query(self):
        matches = _matcher("", "rvt")
        assert matches("anything.rvt") is True
        assert matches("anything.png") is False

    def test_a_leading_dot_on_the_extension_is_optional(self):
        assert _matcher("", ".png")("shot.png") is True

    def test_query_and_extension_must_both_hold(self):
        matches = _matcher("tower", "rvt")
        assert matches("tower.rvt") is True
        assert matches("tower.png") is False
        assert matches("bridge.rvt") is False

    def test_an_empty_search_matches_nothing(self):
        assert _matcher("", "")("anything.txt") is False


class TestItActuallyWalks:
    @pytest.fixture
    def tree(self, tmp_path, monkeypatch):
        wanted = tmp_path / "deep" / "nested"
        wanted.mkdir(parents=True)
        (wanted / "lost-tower.rvt").write_text("x")
        (tmp_path / "noise.txt").write_text("x")
        skipped = tmp_path / "node_modules"
        skipped.mkdir()
        (skipped / "lost-tower.rvt").write_text("x")
        monkeypatch.setattr(find_file, "priority_dirs", lambda: [str(tmp_path)])
        monkeypatch.setattr(find_file, "_fixed_drives", lambda: [])
        return tmp_path

    def test_it_finds_a_file_several_levels_down(self, tree):
        hits, finished = search("lost-tower")
        assert finished is True
        assert any(hit.path.endswith("lost-tower.rvt") for hit in hits)

    def test_results_are_newest_first(self, tree):
        """Every mtime is set explicitly, and that is the point.

        Backdating only one file left the rest sharing an mtime to the
        resolution of the clock — including the copy under ``node_modules``,
        which the priority phase does not skip. The winner of a tie is then
        whatever order the directory happened to be walked in, so this passed
        alone and failed in a full run. A test about ordering must not contain
        a tie.
        """
        newer = tree / "deep" / "newer-tower.rvt"
        newer.write_text("x")
        now = time.time()
        for age, path in (
            (9000, tree / "deep" / "nested" / "lost-tower.rvt"),
            (7000, tree / "node_modules" / "lost-tower.rvt"),
            (0, newer),
        ):
            os.utime(path, (now - age, now - age))
        hits, _ = search("tower")
        assert [os.path.basename(hit.path) for hit in hits] == [
            "newer-tower.rvt",
            "lost-tower.rvt",
            "lost-tower.rvt",
        ]
        assert hits[0].path == str(newer)

    def test_a_missing_priority_directory_is_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            find_file, "priority_dirs", lambda: [str(tmp_path / "gone")]
        )
        monkeypatch.setattr(find_file, "_fixed_drives", lambda: [])
        hits, finished = search("anything")
        assert (hits, finished) == ([], True)


class TestTheBudgetIsSplitBetweenPhases:
    """A shared deadline let phase one eat everything.

    Live: a query with no match spent all 25 seconds inside D:\\Downloads and
    the full-drive walk — the half that answers "it could be anywhere" — never
    ran. Reported as finished, which is the part that makes it a bug rather
    than a slow search.
    """

    def test_the_drive_walk_runs_even_when_the_priority_phase_is_slow(
        self, tmp_path, monkeypatch
    ):
        slow = tmp_path / "slow"
        slow.mkdir()
        drive = tmp_path / "drive"
        drive.mkdir()
        (drive / "target.rvt").write_text("x")

        real_scan = find_file._scan

        def _slow_scan(root, matches, **kwargs):
            if str(root) == str(slow):
                time.sleep(0.4)
                return False
            return real_scan(root, matches, **kwargs)

        monkeypatch.setattr(find_file, "priority_dirs", lambda: [str(slow)])
        monkeypatch.setattr(find_file, "_fixed_drives", lambda: [str(drive)])
        monkeypatch.setattr(find_file, "_scan", _slow_scan)

        hits, _ = search("target", budget_seconds=5.0)
        assert [os.path.basename(hit.path) for hit in hits] == ["target.rvt"]

    def test_running_out_of_time_is_reported_not_hidden(self, tmp_path, monkeypatch):
        monkeypatch.setattr(find_file, "priority_dirs", lambda: [])
        monkeypatch.setattr(find_file, "_fixed_drives", lambda: [str(tmp_path)])
        monkeypatch.setattr(find_file, "_scan", lambda *a, **k: False)
        _, finished = search("anything")
        assert finished is False


class TestTheTool:
    @pytest.fixture
    def sandbox(self, tmp_path, monkeypatch):
        (tmp_path / "found.rvt").write_text("x")
        (tmp_path / "installer.exe").write_text("x")
        monkeypatch.setattr(find_file, "priority_dirs", lambda: [str(tmp_path)])
        monkeypatch.setattr(find_file, "_fixed_drives", lambda: [])
        return tmp_path

    def test_it_needs_something_to_search_for(self):
        result = FindFileTool().execute(query="")
        assert result.success is False

    def test_a_miss_is_not_success(self, sandbox):
        result = FindFileTool().execute(query="nothing-like-this", reveal=False)
        assert result.success is False
        assert result.metadata["count"] == 0

    def test_a_hit_reveals_it_in_explorer(self, sandbox, monkeypatch):
        revealed = []
        monkeypatch.setattr(find_file, "reveal", revealed.append)
        result = FindFileTool().execute(query="found")
        assert result.success is True
        assert revealed and revealed[0].endswith("found.rvt")

    def test_reveal_can_be_turned_off(self, sandbox, monkeypatch):
        monkeypatch.setattr(
            find_file,
            "reveal",
            lambda path: pytest.fail("should not have opened Explorer"),
        )
        assert FindFileTool().execute(query="found", reveal=False).success is True

    def test_it_refuses_to_launch_a_program(self, sandbox, monkeypatch):
        """Revealing an .exe is fine; running one the user has not looked at
        is not."""
        monkeypatch.setattr(find_file, "reveal", lambda path: None)
        monkeypatch.setattr(
            os,
            "startfile",
            lambda path: pytest.fail("launched a program"),
            raising=False,
        )
        result = FindFileTool().execute(query="installer", open=True)
        assert "is a program" in result.content

    def test_a_document_may_be_opened(self, sandbox, monkeypatch):
        opened = []
        monkeypatch.setattr(find_file, "reveal", lambda path: None)
        monkeypatch.setattr(os, "startfile", opened.append, raising=False)
        FindFileTool().execute(query="found", open=True)
        assert opened and opened[0].endswith("found.rvt")

    def test_explorer_failing_does_not_fail_the_search(self, sandbox, monkeypatch):
        def _boom(path):
            raise OSError("no shell")

        monkeypatch.setattr(find_file, "reveal", _boom)
        result = FindFileTool().execute(query="found")
        assert result.success is True
        assert "could not open Explorer" in result.content


class TestRevealSurvivesSpacesInTheName:
    """Explorer re-parses its own command line.

    Live: a Python argument list quoted the whole token as
    ``"/select,C:\a b\\c.png"``, Explorer could not read it, and the window
    opened at Documents instead of the folder holding the file. Every
    screenshot Windows takes has spaces in its name, so this was the common
    case, not an edge one.
    """

    def _command(self, monkeypatch, path):
        seen = {}

        def fake_popen(command, **kwargs):
            seen["command"] = command
            seen["kwargs"] = kwargs
            return None

        monkeypatch.setattr(find_file.subprocess, "Popen", fake_popen)
        find_file.reveal(path)
        return seen

    def test_the_path_is_quoted_but_the_switch_is_not(self, monkeypatch):
        seen = self._command(monkeypatch, r"C:\a b\Screenshot 2026-09-01.png")
        assert seen["command"].startswith("explorer.exe /select,\"")
        assert seen["command"].endswith('.png"')

    def test_it_is_a_string_not_an_argument_list(self, monkeypatch):
        """A list cannot express this quoting, which is the whole bug."""
        seen = self._command(monkeypatch, r"C:\a b\c.png")
        assert isinstance(seen["command"], str)

    def test_no_shell_is_involved(self, monkeypatch):
        seen = self._command(monkeypatch, r"C:\a b\c.png")
        assert seen["kwargs"]["shell"] is False
