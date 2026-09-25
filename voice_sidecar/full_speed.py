"""Ask Windows not to slow this process down as background work.

Sage's server and voice sidecar run with no window, and on a hybrid Intel
CPU on the Balanced plan Windows ran them as background work: a wake-word
check measured 430-570 ms in the server against 135-224 ms for the same code
in a terminal, and opting the running server out took it to 250-273 ms
(25 September). The process says it is latency-sensitive; the power plan
and the user's settings are left alone.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

#: PROCESS_INFORMATION_CLASS ProcessPowerThrottling.
_PROCESS_POWER_THROTTLING = 4
#: PROCESS_POWER_THROTTLING_EXECUTION_SPEED.
_EXECUTION_SPEED = 0x1


def run_at_full_speed() -> bool:
    """Opt this process out of execution-speed throttling. Whether it took."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class _State(ctypes.Structure):
            _fields_ = [
                ("Version", wintypes.ULONG),
                ("ControlMask", wintypes.ULONG),
                ("StateMask", wintypes.ULONG),
            ]

        # Controlled, and the state bit clear: never throttle.
        state = _State(1, _EXECUTION_SPEED, 0)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Declared: undeclared, the pseudo-handle -1 is passed as a 32-bit
        # int and Windows answers "invalid handle".
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetProcessInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetProcessInformation.restype = wintypes.BOOL
        ok = kernel32.SetProcessInformation(
            kernel32.GetCurrentProcess(),
            _PROCESS_POWER_THROTTLING,
            ctypes.byref(state),
            ctypes.sizeof(state),
        )
        if not ok:
            logger.warning(
                "Could not opt out of CPU throttling (error %d)",
                ctypes.get_last_error(),
            )
        return bool(ok)
    except Exception as exc:  # noqa: BLE001 -- a hint, never a failure
        logger.warning("Could not opt out of CPU throttling: %s", exc)
        return False
