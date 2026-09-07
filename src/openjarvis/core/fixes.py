"""Operational fixes for the problems :mod:`openjarvis.core.health` reports.

Two rules shape this module, both from things that have already gone wrong
here.

**Nothing is applied without confirmation.** A read-only checker that quietly
recorded state once cancelled a day of reminders. :func:`apply_fix` refuses
unless ``confirmed=True`` is passed explicitly, so a model that decides on its
own to "just fix it" gets a refusal rather than a side effect.

**A fix that cannot be automated says so instead of pretending.** Re-authorizing
a connector needs a browser and a human at Google's consent screen; there is no
headless path. Those fixes return the steps and the URL to start, and refuse to
"apply". Reporting success for something that did not happen is the failure
this whole milestone exists to prevent.

Fixes are operational only -- re-running a job, restarting a stalled one,
pointing at an authorization flow. Editing source belongs to M33.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Fix ids are ``<action>:<target>``. The health checks emit them; nothing here
# invents one.
REAUTH = "reauth"
RERUN_JOB = "rerun-job"
RESUME_JOB = "resume-job"


@dataclass
class FixPlan:
    """What a fix would do, shown before anything happens."""

    fix_id: str
    title: str
    description: str
    steps: List[str] = field(default_factory=list)
    automatic: bool = False
    reversible: bool = True
    url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FixOutcome:
    """What actually happened. ``applied`` is never optimistic."""

    fix_id: str
    applied: bool
    message: str
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _split(fix_id: str) -> tuple[str, str]:
    action, _, target = fix_id.partition(":")
    return action.strip(), target.strip()


def _scheduler() -> Any:
    """The running TaskScheduler, or None.

    Read from the scheduler tools' class attribute rather than wired
    separately: ``jarvis serve`` hand-assembles its system and does not call
    ``SystemBuilder.build()``, so anything needing its own injection there is
    silently absent. Reusing an injection that already happens avoids that.
    """
    try:
        from openjarvis.scheduler.tools import ListScheduledTasksTool

        return getattr(ListScheduledTasksTool, "_scheduler", None)
    except Exception:
        return None


def _find_task(task_id: str) -> Any:
    scheduler = _scheduler()
    if scheduler is None:
        return None
    try:
        for task in scheduler.list_tasks():
            if str(getattr(task, "id", "") or "") == task_id:
                return task
    except Exception:
        return None
    return None


# -- Plans -------------------------------------------------------------------


def describe_fix(fix_id: str) -> Optional[FixPlan]:
    """Describe what applying *fix_id* would do, without doing any of it."""
    action, target = _split(fix_id)

    if action == REAUTH and target:
        return FixPlan(
            fix_id=fix_id,
            title=f"Re-authorize {target}",
            description=(
                f"{target} has no usable credential, so it returns nothing. "
                "Authorization needs a browser and your approval on the "
                "provider's consent screen, so this cannot be done for you."
            ),
            steps=[
                "Open Data Sources in the web UI.",
                f"Find {target} and start its connection flow.",
                "Approve the requested scopes in the browser.",
                "Run the health check again to confirm it now reports a token.",
            ],
            automatic=False,
            reversible=True,
            url=f"/v1/connectors/{target}/oauth/start",
        )

    if action == RERUN_JOB and target:
        task = _find_task(target)
        prompt = str(getattr(task, "prompt", "") or "")[:80] if task else ""
        return FixPlan(
            fix_id=fix_id,
            title="Run this scheduled job now",
            description=(
                "Runs the job once, immediately, exactly as its schedule "
                "would. It does not change the schedule."
                + (f" Job: {prompt}" if prompt else "")
            ),
            steps=[
                "Execute the job once now.",
                "Its result is written to the run log like any other run.",
            ],
            automatic=True,
            # A job can send a notification or write a file; running one is
            # not undoable, and saying otherwise would be a lie.
            reversible=False,
        )

    if action == RESUME_JOB and target:
        return FixPlan(
            fix_id=fix_id,
            title="Resume this paused job",
            description="Returns the job to its normal schedule.",
            steps=["Set the job back to active."],
            automatic=True,
            reversible=True,
        )

    return None


# -- Application -------------------------------------------------------------


def apply_fix(fix_id: str, *, confirmed: bool = False) -> FixOutcome:
    """Apply *fix_id*, but only when *confirmed* is explicitly True."""
    plan = describe_fix(fix_id)
    if plan is None:
        return FixOutcome(fix_id, False, f"There is no fix called '{fix_id}'.")

    if not confirmed:
        return FixOutcome(
            fix_id,
            False,
            "Not applied: this fix needs explicit confirmation.",
            detail=plan.description,
        )

    if not plan.automatic:
        return FixOutcome(
            fix_id,
            False,
            "This fix cannot be applied automatically.",
            detail=" ".join(plan.steps),
        )

    action, target = _split(fix_id)
    scheduler = _scheduler()
    if scheduler is None:
        return FixOutcome(
            fix_id,
            False,
            "The scheduler is not running, so the job cannot be touched.",
        )

    if action == RERUN_JOB:
        task = _find_task(target)
        if task is None:
            return FixOutcome(fix_id, False, f"No scheduled job with id {target}.")
        try:
            scheduler._execute_task(task)
        except Exception as exc:
            return FixOutcome(
                fix_id, False, f"The job was started but failed: {exc}"
            )
        return FixOutcome(fix_id, True, "The job ran. Check its latest run log.")

    if action == RESUME_JOB:
        try:
            scheduler.resume_task(target)
        except Exception as exc:
            return FixOutcome(fix_id, False, f"Could not resume the job: {exc}")
        return FixOutcome(fix_id, True, "The job is active again.")

    return FixOutcome(fix_id, False, f"'{action}' has no automatic handler.")


__all__ = ["FixOutcome", "FixPlan", "apply_fix", "describe_fix"]
