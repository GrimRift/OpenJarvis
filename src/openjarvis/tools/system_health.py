"""System health tool — Sage answering "is everything working?" for itself.

Every fault this system has had was found by the user noticing: a silently
expired Google token, three APIs disabled at the project level, a briefing
section that collected nothing, a dashboard reading zero, a wake word firing
on a muted microphone. This tool exists so the question can be asked.

It shares :func:`openjarvis.core.health.run_health_checks` with the Health
page and ``jarvis doctor``, so the three surfaces cannot report different
things. It reports; it never repairs. A proposed fix is returned as text for
the user to confirm, because a read-only checker that quietly acted has
already cost this project a day of missed reminders.
"""

from __future__ import annotations

from typing import Any

from openjarvis.core.health import run_health_checks
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


@ToolRegistry.register("system_health")
class SystemHealthTool(BaseTool):
    """Run Sage's own diagnostics and report what is wrong."""

    tool_id = "system_health"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="system_health",
            description=(
                "Check Sage's own health and report what is broken. Use this "
                "for ANY question about whether Sage itself is working: "
                "'check system health', 'run a self-check', 'is everything "
                "working', 'are your connectors okay', 'is the voice pipeline "
                "working', 'why is the briefing empty', 'diagnose yourself'. "
                "Covers the voice pipeline, models and GPU, scheduled jobs, "
                "credentials and connectors. Quote the 'summary' it returns."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "live": {
                        "type": "boolean",
                        "description": (
                            "Also probe paid or quota-limited providers over "
                            "the network. Off by default because those calls "
                            "cost money and count against daily caps. Only "
                            "set this when the user asks for a deep or live "
                            "check."
                        ),
                    },
                },
                "required": [],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        live = bool(params.get("live", False))
        try:
            report = run_health_checks(live=live)
        except Exception as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"The health check itself failed to run: {exc}",
                success=False,
            )

        problems = [c for c in report.checks if c.status != "ok"]
        fixes = [c.fix for c in problems if c.fix]

        lines = [report.summarize()]
        for check in problems:
            if check.details:
                lines.append(f"  {check.name}: {check.details}")
            # The id has to appear in the text, not only the metadata: asked
            # to fix something, the model answered "the health check did not
            # provide a specific fix ID" and stopped, because it never sees
            # metadata. Without this the confirmation flow cannot start.
            if check.fix:
                lines.append(f"  Fix id for {check.name}: {check.fix}")
        if fixes:
            lines.append(
                "Fixes are available. Nothing has been changed. To apply one, "
                "ask the user to approve that specific fix, then call "
                "apply_health_fix with its id and confirmed=true."
            )

        return ToolResult(
            tool_name=self.tool_id,
            content="\n".join(lines),
            success=True,
            metadata={
                "status": report.status,
                "live": report.live,
                "total_checks": len(report.checks),
                "problem_count": len(problems),
                "summary": report.summarize(),
                "available_fixes": fixes,
                "report": report.to_dict(),
            },
        )
