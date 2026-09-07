"""Tests for operational fixes.

These are mostly refusal tests, and deliberately so. The risk in this feature
is not that a fix fails to apply -- it is that something applies without being
asked, or reports success for work that never happened. Both have real
precedent in this codebase.
"""

from __future__ import annotations

from unittest.mock import patch

from openjarvis.core.fixes import apply_fix, describe_fix


class _FakeTask:
    def __init__(self, task_id: str, prompt: str = "do a thing") -> None:
        self.id = task_id
        self.prompt = prompt


class _FakeScheduler:
    def __init__(self, tasks=None, fail: bool = False) -> None:
        self._tasks = tasks or []
        self.executed = []
        self.resumed = []
        self._fail = fail

    def list_tasks(self):
        return self._tasks

    def _execute_task(self, task):
        if self._fail:
            raise RuntimeError("boom")
        self.executed.append(task.id)

    def resume_task(self, task_id):
        self.resumed.append(task_id)


class TestDescribe:
    def test_an_unknown_fix_has_no_plan(self) -> None:
        assert describe_fix("nonsense:whatever") is None
        assert describe_fix("") is None

    def test_reauth_is_described_as_manual(self) -> None:
        plan = describe_fix("reauth:gmail")
        assert plan is not None
        assert plan.automatic is False
        assert plan.steps
        assert plan.url == "/v1/connectors/gmail/oauth/start"

    def test_rerun_is_automatic_but_not_reversible(self) -> None:
        with patch("openjarvis.core.fixes._scheduler", return_value=None):
            plan = describe_fix("rerun-job:abc")
        assert plan is not None
        assert plan.automatic is True
        # A job can notify or write; calling that reversible would be a lie.
        assert plan.reversible is False

    def test_describing_never_runs_anything(self) -> None:
        scheduler = _FakeScheduler(tasks=[_FakeTask("abc")])
        with patch("openjarvis.core.fixes._scheduler", return_value=scheduler):
            describe_fix("rerun-job:abc")
        assert scheduler.executed == []


class TestConfirmationIsRequired:
    def test_nothing_applies_without_confirmation(self) -> None:
        scheduler = _FakeScheduler(tasks=[_FakeTask("abc")])
        with patch("openjarvis.core.fixes._scheduler", return_value=scheduler):
            outcome = apply_fix("rerun-job:abc")
        assert outcome.applied is False
        assert "confirmation" in outcome.message
        assert scheduler.executed == []

    def test_confirmation_false_is_also_a_refusal(self) -> None:
        scheduler = _FakeScheduler(tasks=[_FakeTask("abc")])
        with patch("openjarvis.core.fixes._scheduler", return_value=scheduler):
            outcome = apply_fix("rerun-job:abc", confirmed=False)
        assert outcome.applied is False
        assert scheduler.executed == []

    def test_confirmed_runs_the_job(self) -> None:
        scheduler = _FakeScheduler(tasks=[_FakeTask("abc")])
        with patch("openjarvis.core.fixes._scheduler", return_value=scheduler):
            outcome = apply_fix("rerun-job:abc", confirmed=True)
        assert outcome.applied is True
        assert scheduler.executed == ["abc"]


class TestManualFixesDoNotPretend:
    def test_reauth_refuses_even_when_confirmed(self) -> None:
        outcome = apply_fix("reauth:gmail", confirmed=True)
        assert outcome.applied is False
        assert "cannot be applied automatically" in outcome.message

    def test_an_unknown_fix_is_refused(self) -> None:
        outcome = apply_fix("nonsense:x", confirmed=True)
        assert outcome.applied is False


class TestFailuresAreNotReportedAsSuccess:
    def test_a_raising_job_is_not_applied(self) -> None:
        scheduler = _FakeScheduler(tasks=[_FakeTask("abc")], fail=True)
        with patch("openjarvis.core.fixes._scheduler", return_value=scheduler):
            outcome = apply_fix("rerun-job:abc", confirmed=True)
        assert outcome.applied is False
        assert "boom" in outcome.message

    def test_a_missing_job_is_not_applied(self) -> None:
        scheduler = _FakeScheduler(tasks=[])
        with patch("openjarvis.core.fixes._scheduler", return_value=scheduler):
            outcome = apply_fix("rerun-job:missing", confirmed=True)
        assert outcome.applied is False

    def test_no_scheduler_is_reported_not_assumed(self) -> None:
        with patch("openjarvis.core.fixes._scheduler", return_value=None):
            outcome = apply_fix("rerun-job:abc", confirmed=True)
        assert outcome.applied is False
        assert "scheduler is not running" in outcome.message

    def test_resume_uses_the_scheduler(self) -> None:
        scheduler = _FakeScheduler()
        with patch("openjarvis.core.fixes._scheduler", return_value=scheduler):
            outcome = apply_fix("resume-job:abc", confirmed=True)
        assert outcome.applied is True
        assert scheduler.resumed == ["abc"]


class TestTheTool:
    def _tool(self):
        # Imported directly, not through ToolRegistry: a fixture elsewhere
        # clears the registry between tests, which is not what this is testing.
        from openjarvis.tools.apply_health_fix import ApplyHealthFixTool

        return ApplyHealthFixTool()

    def test_default_call_describes_and_does_not_apply(self) -> None:
        scheduler = _FakeScheduler(tasks=[_FakeTask("abc")])
        with patch("openjarvis.core.fixes._scheduler", return_value=scheduler):
            result = self._tool().execute(fix_id="rerun-job:abc")
        assert result.success is True
        assert result.metadata["applied"] is False
        assert scheduler.executed == []

    def test_confirmed_call_applies(self) -> None:
        scheduler = _FakeScheduler(tasks=[_FakeTask("abc")])
        with patch("openjarvis.core.fixes._scheduler", return_value=scheduler):
            result = self._tool().execute(fix_id="rerun-job:abc", confirmed=True)
        assert result.metadata["applied"] is True
        assert scheduler.executed == ["abc"]

    def test_a_missing_fix_id_is_refused(self) -> None:
        result = self._tool().execute(fix_id="")
        assert result.success is False

    def test_an_unknown_fix_id_is_refused(self) -> None:
        result = self._tool().execute(fix_id="made-up:thing", confirmed=True)
        assert result.success is False
