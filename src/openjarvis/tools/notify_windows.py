"""Notification tool — desktop toast, and any configured channel."""

from __future__ import annotations

import logging
import struct
import subprocess
import sys
import wave
from typing import Any, List, Optional, Sequence, Tuple

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

    Falls back to the built-in Windows synthesiser if the good voice cannot
    be produced or played. A robotic reminder beats a silent one, and silence
    is exactly how this failed the first time: Windows Media Player's COM
    object reported success while never leaving "Transitioning", so the toast
    appeared and nothing was ever heard.
    """
    if not (text or "").strip():
        return False
    if respect_dnd and do_not_disturb():
        logger.info("Do Not Disturb is on; not speaking: %s", text[:60])
        return False

    path = _voice_wav(text)
    if path and _play_wav(path):
        return True
    # Never leave the reminder silent because the nice voice failed. The
    # built-in synthesiser is robotic, but it is always installed and it
    # always makes sound.
    return _speak_builtin(text)


def _voice_wav(text: str) -> Optional[str]:
    """Sage's voice as a 16-bit PCM wav, or None.

    Converted rather than played as-is. Cartesia returns 32-bit float samples
    under a streaming header whose length field is ``ffffffff``, and
    ``SoundPlayer`` rejects that outright as "not a valid wave file"; the
    sample rate has to come from the header too, since assuming 44.1kHz plays
    a 24kHz clip half again too fast.
    """
    try:
        from openjarvis.core.paths import get_config_dir
        from openjarvis.speech.cartesia_tts import CartesiaTTSBackend

        result = CartesiaTTSBackend().synthesize(text, output_format="wav")
        raw = result.audio
        rate = _wav_sample_rate(raw)
        start = raw.find(b"data")
        if rate is None or start < 0:
            return None
        payload = raw[start + 8 :]
        count = len(payload) // 4
        samples = struct.unpack(f"<{count}f", payload[: count * 4])
        samples = _normalised(samples)

        destination = get_config_dir() / "alerts" / "reminder.wav"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(
                b"".join(
                    struct.pack("<h", max(-32768, min(32767, int(value * 32767))))
                    for value in samples
                )
            )
        return str(destination)
    except Exception:
        logger.warning("Could not synthesise the spoken alert", exc_info=True)
        return None


#: Leave a little headroom rather than normalising to the very top, so
#: rounding into 16-bit cannot clip the loudest sample.
_TARGET_PEAK = 0.95

#: A ceiling on the gain. Without one, a clip that is silent or nearly so
#: gets multiplied until its noise floor is the alert.
_MAX_GAIN = 8.0


def _normalised(samples: Sequence[float]) -> Sequence[float]:
    """Scale a voice clip up to a consistent, audible level.

    Cartesia's output is mastered well below full scale -- the reminder
    measured 0.228 of it, some 13 dB down -- which on laptop speakers next to
    a video is quiet enough to miss, and missing it is the entire failure the
    spoken alert exists to prevent. Nothing here is clipping, so this is only
    a gain change: the loudest sample is brought to just under full scale and
    everything else moves with it.
    """
    peak = max((abs(value) for value in samples), default=0.0)
    if peak <= 0.0:
        return samples
    gain = min(_MAX_GAIN, _TARGET_PEAK / peak)
    if gain <= 1.0:
        return samples
    return [value * gain for value in samples]


def _wav_sample_rate(raw: bytes) -> Optional[int]:
    marker = raw.find(b"fmt ")
    if marker < 0 or len(raw) < marker + 16:
        return None
    try:
        return int(struct.unpack("<I", raw[marker + 12 : marker + 16])[0])
    except struct.error:
        return None


def _run_hidden(script: str) -> bool:
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception:
        logger.warning("Could not start the spoken alert", exc_info=True)
        return False


def _play_wav(path: str) -> bool:
    """Play a wav without waiting for it.

    ``SoundPlayer`` rather than Windows Media Player's COM object: the latter
    never left playState 9 (Transitioning) in a non-interactive session, so
    the reminder was silent while every check reported success.
    """
    escaped = path.replace("'", "''")
    return _run_hidden(f"(New-Object Media.SoundPlayer '{escaped}').PlaySync()")


def _speak_builtin(text: str) -> bool:
    escaped = text.replace("'", "''")
    return _run_hidden(
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.Speak('{escaped}')"
    )
