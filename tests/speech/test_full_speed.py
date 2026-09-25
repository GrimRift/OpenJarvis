"""Sage's windowless processes ask Windows not to run them as background
work (25 September: a wake-word check took 430-570 ms throttled, 250-273
opted out)."""

from __future__ import annotations

import sys

import pytest

from openjarvis.core.full_speed import run_at_full_speed


@pytest.mark.skipif(sys.platform != "win32", reason="Windows power throttling")
def test_the_process_is_opted_out_of_throttling():
    import ctypes
    from ctypes import wintypes

    class State(ctypes.Structure):
        _fields_ = [
            ("Version", wintypes.ULONG),
            ("ControlMask", wintypes.ULONG),
            ("StateMask", wintypes.ULONG),
        ]

    assert run_at_full_speed()
    state = State(1, 0, 0)
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
    ]
    assert kernel32.GetProcessInformation(
        kernel32.GetCurrentProcess(), 4, ctypes.byref(state), ctypes.sizeof(state)
    )
    assert state.ControlMask & 1 and not state.StateMask & 1


def test_elsewhere_it_does_nothing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert run_at_full_speed() is False
