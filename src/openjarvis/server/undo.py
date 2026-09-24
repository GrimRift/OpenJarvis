"""Taking back what a reply's tools did when the user stops the reply.

The microphone stays open while Sage prepares an answer, so "stop" can end
it before a word is said (24 September). Stopping the words is not enough
when a tool has already acted -- a video opening, a reminder set -- so each
tool that can be reversed says how, and a turn keeps a ledger of what its
tools did. A tool still running when the turn is stopped is undone the
moment it finishes.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("openjarvis.server")


def _close_tab(result: Any) -> Optional[str]:
    target = str((result.metadata or {}).get("target_id") or "")
    if not target:
        return None
    from openjarvis.tools.cdp import Browser
    from openjarvis.tools.opera_control import DEBUG_PORT, port_is_open

    if not port_is_open():
        return None
    Browser(DEBUG_PORT).close_target(target)
    return "closed the tab it opened"


def _tool(tools: Callable[[str], Any], name: str) -> Any:
    """The turn's own instance of *name*: the server wires state (the
    scheduler) into the instances it builds, not into fresh ones."""
    tool = tools(name)
    if tool is not None:
        return tool
    from openjarvis.core.registry import ToolRegistry

    return ToolRegistry.get(name)()


def _cancel_reminder(result: Any, tools: Callable[[str], Any]) -> Optional[str]:
    watch = (result.metadata or {}).get("watch") or {}
    watch_id = str(watch.get("id") or "")
    if not watch_id:
        return None
    done = _tool(tools, "tell_me_when").execute(action="cancel", watch_id=watch_id)
    return "cancelled the reminder" if done.success else None


def _cancel_task(result: Any, tools: Callable[[str], Any]) -> Optional[str]:
    import json

    task_id = str((result.metadata or {}).get("task_id") or "")
    if not task_id:
        try:
            task_id = str(json.loads(result.content or "{}").get("task_id") or "")
        except (ValueError, AttributeError):
            task_id = ""
    if not task_id:
        return None
    done = _tool(tools, "cancel_scheduled_task").execute(task_id=task_id)
    return "cancelled the scheduled task" if done.success else None


def _pause_music(tools: Callable[[str], Any]) -> Optional[str]:
    done = _tool(tools, "spotify_control").execute(action="pause")
    return "paused the music" if done.success else None


def _spotify(call: Any, result: Any, tools: Callable[[str], Any]) -> Optional[str]:
    import json

    try:
        action = str(json.loads(call.arguments or "{}").get("action") or "")
    except ValueError:
        action = ""
    return _pause_music(tools) if action == "play" else None


#: How to reverse each tool that acts on the world. Anything absent is left
#: as it is: an email read or a search made has nothing to take back.
UNDO: Dict[str, Callable[[Any, Any, Callable[[str], Any]], Optional[str]]] = {
    "youtube_play": lambda call, result, tools: _close_tab(result),
    "netflix_play": lambda call, result, tools: _close_tab(result),
    "web_open": lambda call, result, tools: _close_tab(result),
    "tell_me_when": lambda call, result, tools: _cancel_reminder(result, tools),
    "schedule_task": lambda call, result, tools: _cancel_task(result, tools),
    "spotify_control": _spotify,
}


def _no_tools(name: str) -> Any:
    return None


def undo(
    call: Any, result: Any, tools: Callable[[str], Any] = _no_tools
) -> Optional[str]:
    """Reverse one tool call's effect; what was done, or None."""
    reverse = UNDO.get(getattr(call, "name", ""))
    if reverse is None or not getattr(result, "success", False):
        return None
    try:
        done = reverse(call, result, tools)
    except Exception:  # noqa: BLE001 -- best effort, never raise into a stop
        logger.warning("Could not undo %s", call.name, exc_info=True)
        return None
    if done:
        logger.info("Stopped turn: %s (%s)", done, call.name)
    return done


class TurnLedger:
    """What a turn's tools did, so a stop can take it back."""

    def __init__(self, tools: Callable[[str], Any] = _no_tools) -> None:
        self._tools = tools
        self._lock = threading.Lock()
        self._done: List[Tuple[Any, Any]] = []
        self._stopped = False

    def run(self, execute: Callable[[Any], Any], call: Any) -> Any:
        """Run one tool call (in a worker thread) and record what it did.

        Recorded from the worker itself: a stopped turn stops awaiting its
        tool, but the tool runs on, and must still be undone when it ends.
        """
        result = execute(call)
        with self._lock:
            self._done.append((call, result))
            stopped = self._stopped
        if stopped:
            undo(call, result, self._tools)
        return result

    def stop(self) -> None:
        """The turn was stopped: undo what has run, and whatever finishes."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            done = list(self._done)
        if done:
            threading.Thread(
                target=lambda: [undo(c, r, self._tools) for c, r in reversed(done)],
                name="undo-stopped-turn",
                daemon=True,
            ).start()


__all__ = ["TurnLedger", "UNDO", "undo"]
