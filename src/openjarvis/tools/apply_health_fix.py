"""Apply one of the operational fixes ``system_health`` proposes.

Split from ``system_health`` on purpose. Checking is read-only and safe to
call freely; applying changes the machine. Keeping them in one tool would mean
a model could reach a side effect while "just checking", and this codebase has
already lost a day of reminders to a read path that quietly wrote.

The tool describes by default and only acts when ``confirmed`` is true, which
the user has to say. Nothing here edits source -- that is M33.
"""

from __future__ import annotations

from typing import Any

from openjarvis.core.fixes import apply_fix, describe_fix
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


@ToolRegistry.register("apply_health_fix")
class ApplyHealthFixTool(BaseTool):
    """Describe, and on confirmation apply, a fix from the health report."""

    tool_id = "apply_health_fix"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="apply_health_fix",
            description=(
                "Describe or apply one of the fixes that system_health "
                "proposed, using the exact fix id it returned (for example "
                "'rerun-job:abc123'). Call it WITHOUT confirmed to explain "
                "what the fix would do. Only call it with confirmed=true "
                "after the user has said yes to that specific fix in this "
                "conversation. Never guess a fix id and never confirm on the "
                "user's behalf."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "fix_id": {
                        "type": "string",
                        "description": (
                            "The fix id exactly as system_health returned it."
                        ),
                    },
                    "confirmed": {
                        "type": "boolean",
                        "description": (
                            "True only when the user has explicitly approved "
                            "this specific fix. Defaults to false, which "
                            "describes the fix without applying it."
                        ),
                    },
                },
                "required": ["fix_id"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        fix_id = str(params.get("fix_id") or "").strip()
        if not fix_id:
            return ToolResult(
                tool_name=self.tool_id,
                content="No fix id was given. Run system_health first.",
                success=False,
            )

        confirmed = bool(params.get("confirmed", False))
        plan = describe_fix(fix_id)
        if plan is None:
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    f"There is no fix called '{fix_id}'. Run system_health "
                    "and use one of the fix ids it reports."
                ),
                success=False,
            )

        if not confirmed:
            lines = [plan.title, plan.description]
            if plan.steps:
                lines.append("Steps: " + " ".join(plan.steps))
            lines.append(
                "Nothing has been changed. Say so explicitly if you want this "
                "applied."
                if plan.automatic
                else "This one has to be done by hand; it cannot be applied."
            )
            return ToolResult(
                tool_name=self.tool_id,
                content="\n".join(lines),
                success=True,
                metadata={"applied": False, "plan": plan.to_dict()},
            )

        outcome = apply_fix(fix_id, confirmed=True)
        return ToolResult(
            tool_name=self.tool_id,
            content=outcome.message
            + (f" {outcome.detail}" if outcome.detail else ""),
            success=outcome.applied,
            metadata={"applied": outcome.applied, "outcome": outcome.to_dict()},
        )
