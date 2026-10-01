"""This PC's load for the system panel (M41): labels, the summary, End."""

from __future__ import annotations

import os
import threading
from unittest.mock import MagicMock, patch

import pytest

from openjarvis.core import machine as m


def _snap(**over):
    snap = {
        "cpu": {"percent": 20.0, "threads": 16, "ghz": 2.4},
        "ram": {"percent": 87.3, "used_gb": 13.6, "total_gb": 15.6},
        "gpu": {
            "name": "RTX 5050 Laptop GPU",
            "percent": 44.0,
            "vram_used_gb": 4.2,
            "vram_total_gb": 8.0,
            "temp_c": 65.0,
        },
        "disks": [
            {"name": "C:", "percent": 67.6, "free_gb": 145, "total_gb": 447},
            {"name": "D:", "percent": 91.0, "free_gb": 84, "total_gb": 931},
        ],
        "net": {"down_mbps": 1.2, "up_mbps": 0.1},
        "battery": {"percent": 100, "plugged": True},
        "top_by_memory": [{"name": "Opera GX", "mem_gb": 3.93}],
        "sage_total": {"cpu": 8.1, "mem_gb": 4.5},
    }
    snap.update(over)
    return snap


VOICE = (
    r"C:\AI\OpenJarvis-Data\voice-env\Scripts\python.exe -m voice_sidecar --port 8791"
)
SERVER = (
    r"C:\AI\OpenJarvis-Lab\.venv\Scripts\python.exe "
    r"C:\AI\OpenJarvis-Lab\.venv\Scripts\jarvis.exe serve"
)


class TestWhoIsSage:
    @pytest.mark.parametrize(
        "name,cmd,ancestors,label",
        [
            ("python.exe", VOICE, [], "Sage voice engine"),
            ("python.exe", SERVER, [], "Sage server"),
            (
                "msedgewebview2.exe",
                "msedgewebview2.exe --webview-exe-name=sage-desktop.exe",
                [],
                "Sage app",
            ),
            (
                "msedgewebview2.exe",
                "x --type=renderer",
                ["msedgewebview2.exe", "sage-desktop.exe"],
                "Sage app",
            ),
            ("ollama.exe", "ollama.exe serve", [], "Ollama (Sage's local models)"),
            ("python.exe", r"C:\tools\myscript.py", [], None),
            ("opera.exe", "opera.exe --type=renderer", [], None),
        ],
    )
    def test_labels(self, name, cmd, ancestors, label):
        assert m._sage_label(name, cmd, ancestors) == label

    def test_windows_programs_are_protected(self):
        assert m._protected("svchost.exe", r"C:\Windows\System32\svchost.exe")
        assert m._protected("whatever.exe", r"C:\Windows\SysWOW64\whatever.exe")
        assert not m._protected(
            "opera.exe", r"C:\Users\x\AppData\Local\Programs\Opera GX\opera.exe"
        )


class TestTheSummary:
    def test_it_names_what_is_high(self):
        line = m.summarize(_snap())
        assert line.startswith("Running fine; memory is high at 87% (13.6 of 15.6 GB)")
        assert "drive D: is 91% full (84 GB free)" in line
        assert "Most memory: Opera GX (3.9 GB)" in line
        assert "Sage itself uses 4.5 GB" in line

    def test_a_quiet_machine_says_so(self):
        snap = _snap(
            ram={"percent": 50.0, "used_gb": 7.8, "total_gb": 15.6},
            disks=[{"name": "C:", "percent": 60.0, "free_gb": 170, "total_gb": 447}],
        )
        assert m.summarize(snap).startswith("Running well; nothing is under strain.")

    @pytest.mark.parametrize(
        "asked,start",
        [
            ("memory", "Memory: 87% used (13.6 of 15.6 GB)."),
            ("is my gpu hot", "GPU (RTX 5050 Laptop GPU): 44% busy, 65°C"),
            ("disk", "Drive C: 68% full, 145 GB free."),
            ("battery", "Battery: 100%, plugged in."),
        ],
    )
    def test_the_asked_part_comes_first(self, asked, start):
        assert m.summarize(_snap(), asked).startswith(start)

    def test_the_asked_part_is_not_said_twice(self):
        line = m.summarize(_snap(), "memory")
        assert line.count("87%") == 1
        assert "memory is high" in line

    def test_windows_11_is_named_11(self):
        if m.sys.platform == "win32":
            build = m.sys.getwindowsversion().build
            assert m._os_label() == ("Windows 11" if build >= 22000 else m._os_label())

    def test_part_words(self):
        assert m.part_key("how's my RAM") == "ram"
        assert m.part_key("graphics card temp") == "gpu"
        assert m.part_key("") is None
        assert m.part_key("general") is None


class TestEnding:
    def _machine(self, pid, label):
        mach = m.Machine.__new__(m.Machine)
        mach._scan_lock = threading.Lock()
        proc = MagicMock(pid=pid)
        mach._procs = {pid: proc}
        mach._labels = {pid: label}
        mach._scan = lambda: None  # the fixture's processes are the scan
        return mach, proc

    @pytest.mark.parametrize(
        "label",
        [("Sage voice engine", "Sage voice engine", True), ("Svchost", None, True)],
    )
    def test_sage_and_windows_are_refused(self, label):
        mach, proc = self._machine(424242, label)
        with pytest.raises(PermissionError):
            mach.end(label[0])
        proc.terminate.assert_not_called()

    def test_an_ordinary_program_is_terminated(self):
        mach, proc = self._machine(424242, ("Opera GX", None, False))
        with patch.object(m.psutil, "wait_procs", return_value=([proc], [])):
            assert mach.end("Opera GX") == {
                "app": "Opera GX",
                "ended": 1,
                "still_running": 0,
            }
        proc.terminate.assert_called_once()

    def test_the_server_itself_is_refused_whatever_its_label(self):
        mach, proc = self._machine(os.getpid(), ("Python", None, False))
        with pytest.raises(PermissionError):
            mach.end("Python")
        proc.terminate.assert_not_called()

    def test_end_scans_first(self):
        mach, _ = self._machine(424242, ("Opera GX", None, False))
        scanned = []
        mach._scan = lambda: scanned.append(True)
        with pytest.raises(LookupError):
            mach.end("Notepad")
        assert scanned

    def test_not_running(self):
        mach, _ = self._machine(424242, ("Opera GX", None, False))
        with pytest.raises(LookupError):
            mach.end("Steam")


def test_a_real_snapshot_has_every_part():
    snap = m.Machine().snapshot()
    for key in ("cpu", "ram", "disks", "net", "top_by_memory", "sage_total"):
        assert key in snap
    assert 0 <= snap["ram"]["percent"] <= 100
