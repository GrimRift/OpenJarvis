"""``mark_done``: the user tells Sage a brief item is finished (2 October).

"The PMFC exam is done" -- and the morning briefs stop listing it. The briefs
also drop items on evidence (a sent submission, Teams' Completed tab); this
is the user's own word, which needs no evidence.
"""

from __future__ import annotations

from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


@ToolRegistry.register("mark_done")
class MarkDoneTool(BaseTool):
    """Record that something the briefs keep mentioning is done."""

    tool_id = "mark_done"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="mark_done",
            description=(
                "Record that the user has finished something the morning "
                "briefs keep mentioning, so they stop listing it: 'the PMFC "
                "exam is done', 'I already submitted Activity: Leading', "
                "'that CI failure is fixed'. Use the item's own words. Not "
                "for reminders or scheduled tasks (cancel those instead)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "item": {
                        "type": "string",
                        "description": "What is done, e.g. 'PMFC83 examination'.",
                    },
                },
                "required": ["item"],
            },
            category="productivity",
        )

    def execute(self, **params: Any) -> ToolResult:
        from openjarvis.agents.brief_memory import DONE_KEEP_DAYS, mark_done

        try:
            entry = mark_done(str(params.get("item") or ""))
        except ValueError:
            return ToolResult(
                tool_name=self.tool_id, content="What is done?", success=False
            )
        return ToolResult(
            tool_name=self.tool_id,
            content=(
                f"Noted as done: {entry['item']}. The briefs will leave it out "
                f"(kept for {DONE_KEEP_DAYS} days)."
            ),
            success=True,
        )
