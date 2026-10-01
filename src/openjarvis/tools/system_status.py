"""This PC's load -- CPU, memory, GPU, disks, network, battery, top programs.

Not Sage's own health: that is ``system_health``. The result carries a
snapshot in ``metadata.system``; the app opens the system panel from it
(M41) and keeps it live from ``/v1/system/stats`` while it is open.

Read-only on purpose. Ending a program is only the panel's End button after
the user presses Confirm; the model gets no way to do it.
"""

from __future__ import annotations

from typing import Any

from openjarvis.core.machine import machine, part_key, summarize
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


@ToolRegistry.register("system_status")
class SystemStatusTool(BaseTool):
    """Report how hard this computer is working right now."""

    tool_id = "system_status"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="system_status",
            description=(
                "How hard THIS COMPUTER is working right now: CPU, memory/RAM, "
                "GPU load and temperature, disk space, network speed, battery, "
                "and which programs use the most (Sage's own marked). Use it for "
                "'system status', 'system diagnostics', 'how's my PC', 'is my "
                "laptop slow', 'how much RAM is free', 'is my GPU hot', 'what's "
                "using my memory'. NOT for whether Sage itself works (that is "
                "system_health). The app opens a live system panel, so answer "
                "in one or two spoken sentences from the result."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "part": {
                        "type": "string",
                        "description": (
                            "The part asked about, if any: 'cpu', 'memory', "
                            "'gpu', 'disk', 'network' or 'battery'. The panel "
                            "opens on it. Omit for a general question."
                        ),
                    },
                },
                "required": [],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        part = part_key(str(params.get("part") or ""))
        try:
            snap = machine().snapshot()
        except Exception as exc:  # psutil on an odd machine, a dead driver
            return ToolResult(
                tool_name="system_status",
                content=f"Could not read this PC's status: {exc}",
                success=False,
            )
        summary = summarize(snap, part)
        top = ", ".join(
            f"{r['name']} {r['mem_gb']:.1f} GB" for r in snap["top_by_memory"][:4]
        )
        return ToolResult(
            tool_name="system_status",
            content=f"{summary}\nUsing the most memory: {top}.",
            success=True,
            metadata={"system": {**snap, "summary": summary, "focus": part}},
        )


__all__ = ["SystemStatusTool"]
