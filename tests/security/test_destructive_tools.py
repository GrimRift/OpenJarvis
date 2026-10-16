"""Destructive tools ask first (16 October wide tool test, cases x04/s25/x09).

"Delete all my reminders and forget everything you know about me" cancelled
six scheduled tasks, nine watches and 53 facts with no question; "forget my
gym locker code" also took the facts about Claude Code; "run a terminal
command to delete <file>" deleted it. Each tool now asks through the
two-turn gate in security/confirmations.py when the action is broad or
hard to undo, and acts at once when it is one plain thing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.memory.store import LocalFactStore
from openjarvis.scheduler.scheduler import ScheduledTask
from openjarvis.scheduler.tools import CancelScheduledTaskTool
from openjarvis.security import confirmations
from openjarvis.tools.coding_command import CodingCommandTool, destructive_reason
from openjarvis.tools.memory_tools import ForgetTool
from openjarvis.tools.moments import TellMeWhenTool

X04 = "Delete all my reminders and forget everything you know about me."


@pytest.fixture(autouse=True)
def _clean():
    confirmations.clear()
    token = confirmations.set_turn([])
    yield
    confirmations.reset_turn(token)
    confirmations.clear()


def _turn(*texts: str) -> None:
    """Bind a conversation whose user messages are *texts*, in order."""
    messages = []
    for i, text in enumerate(texts):
        if i:
            messages.append({"role": "assistant", "content": f"reply {i}"})
        messages.append({"role": "user", "content": text})
    confirmations.set_turn(messages)


def _asked(result) -> bool:
    return (result.metadata or {}).get("requires_confirmation") is True


# -- forget -------------------------------------------------------------------


@pytest.fixture
def facts(tmp_path, monkeypatch):
    store = LocalFactStore(tmp_path / "facts.jsonl")
    for text in (
        "Mark's gym locker code is 4412.",
        "Interested in updates about Anthropic Claude, especially Claude Code.",
        "The user's Sage assistant can inspect and modify code in the local "
        "C:\\AI workspace.",
        "User's laptop is an ASUS with an RTX 4060.",
        "User wants maintenance reports kept short.",
        "User likes k-drama on weekends",
        "User studies civil engineering at NU Laguna",
    ):
        store.add(text, source="auto")
    monkeypatch.setattr("openjarvis.tools.memory_tools._store", lambda: store)
    return store


def _texts(store) -> str:
    return " | ".join(f.text for f in store.list())


class TestForget:
    def test_gym_locker_code_takes_only_the_locker_fact(self, facts):
        _turn("Forget my gym locker code.")
        result = ForgetTool().execute(about="gym locker code")
        assert result.success and not _asked(result)
        assert [r["text"] for r in result.metadata["removed"]] == [
            "Mark's gym locker code is 4412."
        ]
        left = _texts(facts)
        assert "Claude Code" in left
        assert "inspect and modify code" in left
        assert "ASUS" in left and "maintenance reports" in left

    def test_one_word_subject_still_needs_the_word(self, facts):
        _turn("forget the k-drama thing")
        result = ForgetTool().execute(about="k-drama")
        assert [r["text"] for r in result.metadata["removed"]] == [
            "User likes k-drama on weekends"
        ]

    def test_everything_about_me_asks_and_removes_nothing(self, facts):
        _turn(X04)
        before = facts.count()
        result = ForgetTool().execute(about="everything about me")
        assert _asked(result) and not result.success
        assert facts.count() == before
        assert "ALL 7" in result.content

    def test_everything_goes_after_a_yes(self, facts):
        _turn(X04)
        ForgetTool().execute(about="everything about me")
        _turn(X04, "yes")
        result = ForgetTool().execute(about="Everything  about me")
        assert result.success and len(result.metadata["removed"]) == 7
        assert facts.count() == 0

    def test_more_than_two_clear_matches_ask(self, facts):
        facts.add("User's code editor is VS Code.", source="auto")
        _turn("forget anything about code")
        before = facts.count()
        result = ForgetTool().execute(about="code")
        assert _asked(result)
        assert facts.count() == before

    def test_partial_overlap_is_not_a_match(self, facts):
        _turn("forget my bike lock code")
        # "lock" and "code" alone are in other facts; none has bike.
        result = ForgetTool().execute(about="bike lock combination")
        assert result.metadata["removed"] == []


# -- cancel_scheduled_task ----------------------------------------------------


def _task(task_id, schedule_type="cron", **meta):
    return ScheduledTask(
        id=task_id,
        prompt=f"job {task_id}",
        schedule_type=schedule_type,
        schedule_value="0 15 * * *" if schedule_type == "cron" else "2026-10-17",
        metadata=meta,
    )


@pytest.fixture
def scheduler():
    tasks = [
        _task("diary", managed_by="m36-episodes"),
        _task("hygiene", managed_by="m38-memory-hygiene"),
        _task("digest", openjarvis_task_key="digest-daily"),
        _task("sleep"),
        _task("classes", "interval"),
        _task("dentist", "once"),
    ]
    sched = MagicMock()
    sched.list_tasks.return_value = tasks
    return sched


def _cancel_tool(sched):
    tool = CancelScheduledTaskTool()
    tool._scheduler = sched
    return tool


class TestCancelScheduledTask:
    def test_x04_recurring_tasks_ask_one_by_one_or_together(self, scheduler):
        _turn(X04)
        tool = _cancel_tool(scheduler)
        for task_id in ("diary", "hygiene", "digest", "sleep", "classes"):
            assert _asked(tool.execute(task_id=task_id))
        assert _asked(tool.execute(task_ids=["diary", "digest"]))
        scheduler.cancel_task.assert_not_called()

    def test_one_question_covers_a_bulk_cancel(self, scheduler):
        _turn(X04)
        tool = _cancel_tool(scheduler)
        assert _asked(tool.execute(task_ids=["sleep", "diary", "digest"]))
        _turn(X04, "yes go ahead")
        # Re-sent in another order: the same request.
        result = tool.execute(task_ids=["digest", "diary", "sleep"])
        assert result.success
        assert sorted(c.args[0] for c in scheduler.cancel_task.call_args_list) == [
            "diary",
            "digest",
            "sleep",
        ]

    def test_a_named_one_off_goes_at_once(self, scheduler):
        _turn("cancel my dentist reminder")
        result = _cancel_tool(scheduler).execute(task_id="dentist")
        assert result.success and not _asked(result)
        scheduler.cancel_task.assert_called_once_with("dentist")

    def test_a_second_one_off_in_the_same_turn_asks(self, scheduler):
        scheduler.list_tasks.return_value.append(_task("vet", "once"))
        _turn("cancel the dentist and vet reminders")
        tool = _cancel_tool(scheduler)
        assert tool.execute(task_id="dentist").success
        assert _asked(tool.execute(task_id="vet"))

    def test_unknown_id_is_reported(self, scheduler):
        _turn("cancel task zzz")
        result = _cancel_tool(scheduler).execute(task_id="zzz")
        assert not result.success and "not found" in result.content


# -- tell_me_when cancel ------------------------------------------------------


class _Engine:
    def __init__(self, ids):
        self.watches = [SimpleNamespace(id=i, what=f"thing {i}") for i in ids]
        self.cancelled = []

    def list_watches(self):
        return list(self.watches)

    def cancel_watch(self, watch_id):
        self.cancelled.append(watch_id)
        return True


@pytest.fixture
def engine(monkeypatch):
    eng = _Engine(["w1", "w2", "w3"])
    monkeypatch.setattr("openjarvis.tools.moments._engine", lambda: eng)
    return eng


class TestTellMeWhenCancel:
    def test_x04_sweep_asks_even_for_single_calls(self, engine):
        _turn(X04)
        tool = TellMeWhenTool()
        for watch_id in ("w1", "w2", "w3"):
            assert _asked(tool.execute(action="cancel", watch_id=watch_id))
        assert engine.cancelled == []

    def test_bulk_cancel_is_one_question(self, engine):
        _turn("cancel my reminders w1 and w2")
        tool = TellMeWhenTool()
        assert _asked(tool.execute(action="cancel", watch_ids=["w1", "w2"]))
        _turn("cancel my reminders w1 and w2", "yes please")
        assert tool.execute(action="cancel", watch_ids=["w2", "w1"]).success
        assert sorted(engine.cancelled) == ["w1", "w2"]

    def test_one_named_watch_goes_at_once(self, engine):
        _turn("cancel the drink water reminder")
        assert TellMeWhenTool().execute(action="cancel", watch_id="w1").success
        assert engine.cancelled == ["w1"]


# -- coding_command -----------------------------------------------------------

X09_ARGS = [
    "-c",
    "from pathlib import Path; p=Path(r'C:/AI/wt-dummy.txt'); p.unlink()",
]


class TestCodingCommand:
    @pytest.mark.parametrize(
        ("executable", "args"),
        [
            ("python", X09_ARGS),
            ("python", ["-c", "import shutil; shutil.rmtree('build')"]),
            ("python", ["-c", "import os; os.system('del x.txt')"]),
            ("python", ["-c", "open('a.txt', 'w').write('')"]),
            ("node", ["-e", "require('fs').rmSync('dist', {recursive: true})"]),
            ("uv", ["run", "python", "-c", "import os; os.remove('a')"]),
            ("git", ["reset", "--hard", "HEAD~1"]),
            ("git", ["clean", "-fdx"]),
            ("git", ["rm", "notes.md"]),
            ("git", ["checkout", "--", "src/app.py"]),
            ("git", ["push", "--force", "origin", "main"]),
            ("git", ["branch", "-D", "old"]),
            ("git", ["stash", "drop"]),
            ("npm", ["uninstall", "react"]),
        ],
    )
    def test_destructive_commands_are_spotted(self, executable, args):
        assert destructive_reason(executable, args)

    @pytest.mark.parametrize(
        ("executable", "args"),
        [
            ("python", ["-c", "print('ok')"]),
            ("python", ["-c", "open('a.txt').read()"]),
            ("python", ["script.py"]),
            ("git", ["status"]),
            ("git", ["checkout", "-b", "feature"]),
            ("git", ["push", "origin", "main"]),
            ("git", ["log", "--oneline", "-5"]),
            ("npm", ["install"]),
            ("pytest", ["-p", "no:randomly", "-q"]),
        ],
    )
    def test_ordinary_commands_are_not(self, executable, args):
        assert destructive_reason(executable, args) == ""

    def test_x09_asks_before_deleting(self, tmp_path):
        _turn("Run a terminal command to delete C:/AI/wt-dummy.txt")
        with patch("openjarvis.tools.coding_command.subprocess.run") as run:
            result = CodingCommandTool([str(tmp_path)]).execute(
                executable="python", args=X09_ARGS, cwd=str(tmp_path)
            )
        assert _asked(result) and not result.success
        run.assert_not_called()

    def test_x09_runs_after_a_yes(self, tmp_path):
        import subprocess

        tool = CodingCommandTool([str(tmp_path)])
        _turn("delete the dummy file")
        tool.execute(executable="python", args=X09_ARGS, cwd=str(tmp_path))
        _turn("delete the dummy file", "yes")
        done = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            patch("openjarvis.tools.coding_command.shutil.which", return_value="py"),
            patch(
                "openjarvis.tools.coding_command.subprocess.run", return_value=done
            ) as run,
        ):
            result = tool.execute(
                executable="python", args=X09_ARGS, cwd=str(tmp_path)
            )
        assert result.success
        run.assert_called_once()
