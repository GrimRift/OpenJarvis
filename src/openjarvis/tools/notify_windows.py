"""Notification tool — desktop toast, and any configured channel."""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Any, List, Tuple

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)


def _send_toast(title: str, message: str, *, duration: str = "short") -> None:
    # Use notify(), not toast() — toast() blocks until the notification is
    # clicked or dismissed (it awaits activation/dismissal futures), which
    # would hang this tool indefinitely waiting on the user. notify() builds
    # and shows the toast synchronously and returns immediately.
    from win11toast import notify

    notify(title, message, duration=duration)


def _send_to_channel(title: str, message: str) -> bool:
    """Send the notification to ``[notifications] channel``, if configured."""
    from openjarvis.core.config import load_config

    spec = (load_config().notifications.channel or "").strip()
    if not spec:
        return False

    from openjarvis.agents.proactive_agent import _build_notification_channel

    channel = _build_notification_channel(spec)
    if channel is None:
        logger.warning("Notification channel %r could not be built", spec)
        return False
    destination = spec.partition(":")[2]
    return bool(channel.send(destination, f"{title}\n\n{message}"))


def deliver(title: str, message: str, *, duration: str = "short") -> List[str]:
    """Deliver a notification everywhere configured; return what succeeded.

    A desktop toast is no use when the user is away from the machine, which
    is the moment a proactive notification matters most — so the channel is
    an addition, not an alternative, and either destination succeeding is
    enough. Raises only if every configured destination fails, so a muted
    desktop does not make a delivered phone notification look like an error.
    """
    delivered: List[str] = []
    failures: List[Tuple[str, Exception]] = []

    from openjarvis.core.config import load_config

    try:
        desktop_enabled = load_config().notifications.desktop
    except Exception:
        desktop_enabled = True

    if desktop_enabled:
        try:
            _send_toast(title, message, duration=duration)
            delivered.append("desktop")
        except Exception as exc:
            failures.append(("desktop", exc))

    try:
        if _send_to_channel(title, message):
            delivered.append("channel")
    except Exception as exc:
        failures.append(("channel", exc))

    if not delivered and failures:
        raise failures[0][1]
    return delivered


@ToolRegistry.register("notify_windows")
class NotifyWindowsTool(BaseTool):
    """Send a native Windows toast notification."""

    tool_id = "notify_windows"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="notify_windows",
            description=(
                "Pop a native Windows toast notification with a title and "
                "message. Use for proactively alerting the user about "
                "something time-sensitive."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Notification title.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Notification body text.",
                    },
                    "duration": {
                        "type": "string",
                        "description": "How long the toast stays visible.",
                        "enum": ["short", "long"],
                    },
                },
                "required": ["title", "message"],
            },
            category="notification",
            timeout_seconds=10.0,
        )

    def execute(self, **params: Any) -> ToolResult:
        title = params.get("title", "")
        message = params.get("message", "")
        duration = params.get("duration", "short")

        if not title or not message:
            return ToolResult(
                tool_name="notify_windows",
                content="Both title and message are required.",
                success=False,
            )

        try:
            delivered = deliver(title, message, duration=duration)
        except Exception as exc:
            return ToolResult(
                tool_name="notify_windows",
                content=f"Failed to send notification: {exc}",
                success=False,
            )

        where = ", ".join(delivered) if delivered else "nowhere (none configured)"
        return ToolResult(
            tool_name="notify_windows",
            content=f"Notification sent to {where}.",
            success=bool(delivered),
            metadata={"title": title, "message": message, "delivered": delivered},
        )


# --- Spoken alerts -------------------------------------------------------

#: Windows writes this only once Do Not Disturb has been toggled at least
#: once, so an absent value means notifications are allowed.
_DND_KEY = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"
_DND_VALUE = "NOC_GLOBAL_SETTING_TOASTS_ENABLED"

#: A spoken reminder is a sentence; nothing here should outlive that by much.
_SPEAK_TIMEOUT_SECONDS = 30


def do_not_disturb() -> bool:
    """Whether Windows is currently suppressing notifications.

    Reads the same switch the Action Center's Do Not Disturb toggle writes.
    Fails open — an unreadable registry means "not suppressed", because a
    reminder that stays silent on a broken lookup is the failure the user
    would notice, and an extra spoken alert is the one they would not mind.
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _DND_KEY) as key:
            enabled, _ = winreg.QueryValueEx(key, _DND_VALUE)
        return int(enabled) == 0
    except (OSError, ValueError, TypeError):
        return False


def speak(text: str, *, respect_dnd: bool = True) -> bool:
    """Say *text* aloud in Sage's own voice. Returns whether it spoke.

    Silent under Do Not Disturb by default: the toast is still delivered, so
    the reminder is not lost — only the noise is.

    Playback goes through Windows Media Player's COM object rather than a
    bundled player, because neither ffplay nor ffmpeg is installed here and
    ``os.startfile`` would pop a visible media window for a one-line
    reminder. Launched without waiting, so a scheduled check is not held open
    for the length of the sentence.
    """
    if not (text or "").strip():
        return False
    if respect_dnd and do_not_disturb():
        logger.info("Do Not Disturb is on; not speaking: %s", text[:60])
        return False

    try:
        from openjarvis.core.paths import get_config_dir
        from openjarvis.tools.text_to_speech import TextToSpeechTool

        result = TextToSpeechTool().execute(
            text=text, output_dir=str(get_config_dir() / "alerts")
        )
        audio_path = (result.metadata or {}).get("audio_path", "")
        if not result.success or not audio_path:
            return False
    except Exception:
        logger.warning("Could not synthesise the spoken alert", exc_info=True)
        return False

    script = (
        "$p = New-Object -ComObject WMPlayer.OCX; "
        f"$p.URL = '{audio_path}'; "
        "$p.controls.play(); "
        "Start-Sleep -Milliseconds 400; "
        f"$limit = (Get-Date).AddSeconds({_SPEAK_TIMEOUT_SECONDS}); "
        "while ($p.playState -eq 3 -and (Get-Date) -lt $limit) "
        "{ Start-Sleep -Milliseconds 200 }"
    )
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        logger.warning("Could not play the spoken alert", exc_info=True)
        return False
    return True
