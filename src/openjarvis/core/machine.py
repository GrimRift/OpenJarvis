"""This PC's load right now, for the system panel (M41).

CPU, memory, GPU, disks, network, battery and the programs using the most,
with Sage's own programs marked. Not Sage's health -- that is
``core/health.py`` and the ``system_health`` tool; this is the machine.

One snapshot is cheap enough to take every second while the panel is open:
process CPU is measured since the previous snapshot (psutil keeps the last
reading on each Process object, so the objects are kept between calls), and
the GPU is read from ``nvidia-smi`` at most once a second.
"""

from __future__ import annotations

import os
import platform
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import psutil

#: Above these a part is "high": the panel turns it amber and Sage mentions it.
HIGH = {"cpu": 90.0, "ram": 85.0, "gpu": 90.0, "gpu_temp": 85.0, "disk": 90.0}

#: Seconds between process scans (the expensive part of a snapshot).
PROCESS_REFRESH_S = 3.0

#: Windows' own programs. Never offered an End button, whatever they use.
_WINDOWS_NAMES = {
    "system",
    "registry",
    "memory compression",
    "idle",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "dwm.exe",
    "explorer.exe",
    "fontdrvhost.exe",
    "sihost.exe",
    "ctfmon.exe",
    "msmpeng.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "runtimebroker.exe",
    "taskhostw.exe",
    "audiodg.exe",
    "spoolsv.exe",
    "lsaiso.exe",
    "securityhealthservice.exe",
    "nissrv.exe",
    "conhost.exe",
}

#: Process names that can belong to Sage; only these get the slow checks.
_MAYBE_SAGE = {
    "python.exe",
    "pythonw.exe",
    "node.exe",
    "msedgewebview2.exe",
    "jarvis.exe",
}

#: Friendly names for programs people recognise by another name.
_FRIENDLY = {
    "opera.exe": "Opera GX",
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "code.exe": "VS Code",
    "chatgpt.exe": "ChatGPT",
    "claude.exe": "Claude",
    "discord.exe": "Discord",
    "spotify.exe": "Spotify",
    "steam.exe": "Steam",
    "msmpeng.exe": "Windows Defender",
    "explorer.exe": "File Explorer",
    "ollama.exe": "Ollama (Sage's local models)",
}


def _sage_label(name: str, cmd: str, ancestors: List[str]) -> Optional[str]:
    """Which part of Sage a process is, or None if it is not Sage's."""
    low = cmd.lower()
    if (
        name == "sage-desktop.exe"
        or "sage-desktop.exe" in ancestors
        or "webview-exe-name=sage-desktop.exe" in low
    ):
        return "Sage app"
    if name == "ollama.exe" or name == "ollama app.exe":
        return "Ollama (Sage's local models)"
    sage_tree = "openjarvis-lab" in low or "sage-staging" in low or "openjarvis" in low
    if "voice_sidecar" in low or "voice-env" in low or "-m voice" in low:
        return "Sage voice engine"
    if sage_tree and ("jarvis.exe" in low or " serve" in low):
        return "Sage server"
    if sage_tree and name == "node.exe" and "vite" in low:
        return "Sage web (Vite)"
    return None


def _os_label() -> str:
    """'Windows 11': Windows 11 still reports release "10"; its build is 22000+."""
    if sys.platform != "win32":
        return platform.platform()
    try:
        build = sys.getwindowsversion().build  # type: ignore[attr-defined]
    except AttributeError:
        return f"Windows {platform.release()}"
    return "Windows 11" if build >= 22000 else f"Windows {platform.release()}"


def _protected(name: str, exe: str) -> bool:
    if name.lower() in _WINDOWS_NAMES:
        return True
    windir = os.environ.get("SystemRoot", r"C:\Windows").lower()
    return bool(exe) and exe.lower().startswith(windir)


class Machine:
    """Takes snapshots; keeps what has to persist between them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: Dict[int, psutil.Process] = {}
        self._net: Optional[Tuple[float, int, int]] = None
        self._gpu: Tuple[float, Optional[Dict[str, Any]]] = (0.0, None)
        self._labels: Dict[int, Tuple[str, Optional[str], bool]] = {}
        self._rows: Tuple[float, List[Dict[str, Any]], Dict[str, float]] = (0.0, [], {})
        # _procs/_labels belong to the scan (background thread) and end().
        self._scan_lock = threading.Lock()
        self._scanning = threading.Event()
        psutil.cpu_percent(None)

    # -- parts ------------------------------------------------------------

    def _gpu_reading(self) -> Optional[Dict[str, Any]]:
        at, last = self._gpu
        if time.monotonic() - at < 1.0:
            return last
        reading: Optional[Dict[str, Any]] = None
        try:
            out = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout.strip()
            name, util, used, total, temp = [
                p.strip() for p in out.splitlines()[0].split(",")
            ]
            reading = {
                "name": name.replace("NVIDIA GeForce ", ""),
                "percent": float(util),
                "vram_used_gb": round(float(used) / 1024, 1),
                "vram_total_gb": round(float(total) / 1024, 1),
                "temp_c": float(temp),
            }
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            reading = None
        self._gpu = (time.monotonic(), reading)
        return reading

    def _net_rates(self) -> Dict[str, float]:
        now = psutil.net_io_counters()
        stamp = time.monotonic()
        rates = {"down_mbps": 0.0, "up_mbps": 0.0}
        if self._net is not None:
            at, recv, sent = self._net
            dt = max(0.001, stamp - at)
            rates = {
                "down_mbps": round(max(0, now.bytes_recv - recv) / dt / 1e6, 2),
                "up_mbps": round(max(0, now.bytes_sent - sent) / dt / 1e6, 2),
            }
        self._net = (stamp, now.bytes_recv, now.bytes_sent)
        return rates

    def _disks(self) -> List[Dict[str, Any]]:
        disks = []
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                use = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            disks.append(
                {
                    "name": part.mountpoint.rstrip("\\/") or part.mountpoint,
                    "percent": round(use.percent, 1),
                    "free_gb": round(use.free / 2**30),
                    "total_gb": round(use.total / 2**30),
                }
            )
        return disks

    def _label(self, proc: psutil.Process) -> Tuple[str, Optional[str], bool]:
        """(app name, Sage part or None, protected) -- cached per pid."""
        pid = proc.pid
        if pid in self._labels:
            return self._labels[pid]
        name, cmd, exe, ancestors = "?", "", "", []
        try:
            name = (proc.name() or "?").lower()
            exe = proc.exe() or ""
            # Command lines and parents are slow to read (the first scan took
            # 7.5 s reading them for every process); only these can be Sage's.
            if name in _MAYBE_SAGE:
                cmd = " ".join(proc.cmdline() or [])
                if name == "msedgewebview2.exe":
                    ancestors = [p.name().lower() for p in proc.parents()[:4]]
        except (psutil.Error, OSError):
            pass
        sage = _sage_label(name, cmd, ancestors)
        app = sage or _FRIENDLY.get(name) or name.removesuffix(".exe").capitalize()
        protected = bool(sage) or _protected(name, exe)
        self._labels[pid] = (app, sage, protected)
        return self._labels[pid]

    def _processes(self) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
        """Programs grouped by app (Opera's many processes are one row)."""
        cores = psutil.cpu_count() or 1
        groups: Dict[str, Dict[str, Any]] = {}
        alive = set()
        for proc in psutil.process_iter(["memory_info"]):
            pid = proc.pid
            alive.add(pid)
            kept = self._procs.setdefault(pid, proc)
            try:
                cpu = kept.cpu_percent(None) / cores
                info = proc.info.get("memory_info")
                mem = info.rss if info is not None else 0
            except (psutil.Error, OSError):
                continue
            if pid in (0, 4):
                continue
            app, sage, protected = self._label(kept)
            g = groups.setdefault(
                app,
                {
                    "name": app,
                    "cpu": 0.0,
                    "mem_gb": 0.0,
                    "pids": [],
                    "sage": bool(sage),
                    "can_end": not protected,
                },
            )
            g["cpu"] += cpu
            g["mem_gb"] += mem / 2**30
            g["pids"].append(pid)
            g["can_end"] = g["can_end"] and not protected
        for pid in list(self._procs):
            if pid not in alive:
                self._procs.pop(pid, None)
                self._labels.pop(pid, None)
        rows = []
        for g in groups.values():
            rows.append(
                {
                    "name": g["name"],
                    "cpu": round(g["cpu"], 1),
                    "mem_gb": round(g["mem_gb"], 2),
                    "count": len(g["pids"]),
                    "sage": g["sage"],
                    "can_end": g["can_end"],
                }
            )
        sage_total: Dict[str, float] = {
            "cpu": round(sum(r["cpu"] for r in rows if r["sage"]), 1),
            "mem_gb": round(sum(r["mem_gb"] for r in rows if r["sage"]), 1),
        }
        return rows, sage_total

    def _scan(self) -> None:
        with self._scan_lock:
            rows, sage_total = self._processes()
            self._rows = (time.monotonic(), rows, sage_total)

    def _scan_soon(self) -> None:
        if self._scanning.is_set():
            return
        self._scanning.set()

        def run() -> None:
            try:
                self._scan()
            finally:
                self._scanning.clear()

        threading.Thread(target=run, name="machine-scan", daemon=True).start()

    # -- snapshot ---------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            vm = psutil.virtual_memory()
            freq = psutil.cpu_freq()
            battery = None
            try:
                b = psutil.sensors_battery()
                if b is not None:
                    battery = {
                        "percent": round(b.percent),
                        "plugged": bool(b.power_plugged),
                    }
            except (AttributeError, OSError):
                battery = None
            # Reading ~330 processes takes ~1 s on this PC, so the program
            # list is scanned in the background every PROCESS_REFRESH_S and a
            # snapshot never waits for it (except the very first).
            at, rows, sage_total = self._rows
            if not rows:
                self._scan()
                at, rows, sage_total = self._rows
            elif time.monotonic() - at >= PROCESS_REFRESH_S:
                self._scan_soon()
            by_mem = sorted(rows, key=lambda r: -r["mem_gb"])[:6]
            by_cpu = sorted(rows, key=lambda r: -r["cpu"])[:6]
            return {
                "host": socket.gethostname(),
                "os": _os_label(),
                "uptime_s": int(time.time() - psutil.boot_time()),
                "taken_at": time.time(),
                "cpu": {
                    "percent": round(psutil.cpu_percent(None), 1),
                    "threads": psutil.cpu_count() or 0,
                    "ghz": round((freq.current if freq else 0) / 1000, 1),
                },
                "ram": {
                    "percent": round(vm.percent, 1),
                    "used_gb": round((vm.total - vm.available) / 2**30, 1),
                    "total_gb": round(vm.total / 2**30, 1),
                },
                "gpu": self._gpu_reading(),
                "disks": self._disks(),
                "net": self._net_rates(),
                "battery": battery,
                "top_by_memory": by_mem,
                "top_by_cpu": by_cpu,
                "sage_total": sage_total,
            }

    # -- ending a program (the panel's End button, after the user confirms) --

    def end(self, app: str) -> Dict[str, Any]:
        """End every process of *app*. Refuses Sage's own and Windows' programs.

        Called only from the panel's Confirm button. There is deliberately no
        tool for this: the model can show what is running, never end it.
        """
        # A fresh scan: the last one may predate the program (1 Oct: Notepad
        # opened after the scan was "not running").
        self._scan()
        with self._scan_lock:
            targets: List[psutil.Process] = []
            for proc in list(self._procs.values()):
                name, _sage, protected = self._label(proc)
                if name != app:
                    continue
                if protected:
                    raise PermissionError(
                        f"{app} is protected and cannot be ended here."
                    )
                targets.append(proc)
        if not targets:
            raise LookupError(f"{app} is not running.")
        # Belt and braces: never the server itself or what started it.
        own = {os.getpid()} | {p.pid for p in psutil.Process().parents()}
        if any(t.pid in own for t in targets):
            raise PermissionError(f"{app} is part of Sage and cannot be ended here.")
        for proc in targets:
            try:
                proc.terminate()
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied as exc:
                raise PermissionError(f"Windows would not let Sage end {app}.") from exc
        _, still = psutil.wait_procs(targets, timeout=4)
        return {
            "app": app,
            "ended": len(targets) - len(still),
            "still_running": len(still),
        }


def summarize(snap: Dict[str, Any], part: Optional[str] = None) -> str:
    """One line for Sage to say and the panel to show under "SAGE"."""
    cpu, ram, gpu = snap["cpu"], snap["ram"], snap.get("gpu")
    # The asked part already leads the line; saying it again reads as a stutter
    # ("Memory: 87% used ... memory is high at 87% ...", 1 Oct).
    asked = part_key(part)
    high: List[str] = []
    if ram["percent"] >= HIGH["ram"]:
        if asked == "ram":
            high.append("memory is high")
        else:
            used = f"{ram['used_gb']} of {ram['total_gb']} GB"
            high.append(f"memory is high at {round(ram['percent'])}% ({used})")
    if cpu["percent"] >= HIGH["cpu"] and asked != "cpu":
        high.append(f"the CPU is busy at {round(cpu['percent'])}%")
    if gpu and gpu["percent"] >= HIGH["gpu"] and asked != "gpu":
        high.append(f"the GPU is busy at {round(gpu['percent'])}%")
    if gpu and gpu["temp_c"] >= HIGH["gpu_temp"] and asked != "gpu":
        high.append(f"the GPU is hot at {round(gpu['temp_c'])}°C")
    for d in snap.get("disks") or []:
        if d["percent"] >= HIGH["disk"] and asked != "disk":
            full = f"{round(d['percent'])}% full ({d['free_gb']} GB free)"
            high.append(f"drive {d['name']} is {full}")
    lead = _part_line(snap, part)
    biggest = (snap.get("top_by_memory") or [None])[0]
    line = (
        ("Running fine; " + "; ".join(high) + ".")
        if high
        else "Running well; nothing is under strain."
    )
    if ram["percent"] >= HIGH["ram"] and biggest:
        line += f" Most memory: {biggest['name']} ({biggest['mem_gb']:.1f} GB)."
    sage = snap.get("sage_total") or {}
    if sage.get("mem_gb"):
        line += f" Sage itself uses {sage['mem_gb']} GB."
    line = line[0].upper() + line[1:]
    return f"{lead} {line}" if lead else line


#: Words a question uses for each part, mapped to the part's key.
PARTS = {
    "cpu": "cpu",
    "processor": "cpu",
    "memory": "ram",
    "ram": "ram",
    "gpu": "gpu",
    "graphics": "gpu",
    "disk": "disk",
    "drive": "disk",
    "storage": "disk",
    "network": "network",
    "internet": "network",
    "battery": "battery",
}


def part_key(asked: Optional[str]) -> Optional[str]:
    """'memory' -> 'ram', 'GPU' -> 'gpu'; None for a general question."""
    text = (asked or "").strip().lower()
    for word, key in PARTS.items():
        if word in text:
            return key
    return None


def _part_line(snap: Dict[str, Any], part: Optional[str]) -> str:
    """The asked-about part first: "Memory: 89% used (13.9 of 15.6 GB)." """
    key = part_key(part)
    gpu = snap.get("gpu")
    if key == "cpu":
        return f"CPU: {round(snap['cpu']['percent'])}% busy."
    if key == "ram":
        r = snap["ram"]
        used = f"{r['used_gb']} of {r['total_gb']} GB"
        return f"Memory: {round(r['percent'])}% used ({used})."
    if key == "gpu" and gpu:
        return (
            f"GPU ({gpu['name']}): {round(gpu['percent'])}% busy, "
            f"{round(gpu['temp_c'])}°C, "
            f"{gpu['vram_used_gb']} of {gpu['vram_total_gb']} GB video memory."
        )
    if key == "disk":
        return " ".join(
            f"Drive {d['name']} {round(d['percent'])}% full, {d['free_gb']} GB free."
            for d in snap.get("disks") or []
        )
    if key == "network":
        n = snap["net"]
        return f"Network: {n['down_mbps']} MB/s down, {n['up_mbps']} MB/s up."
    if key == "battery":
        b = snap.get("battery")
        if not b:
            return "Battery: none on this PC."
        power = "plugged in" if b["plugged"] else "on battery"
        return f"Battery: {b['percent']}%, {power}."
    return ""


_machine: Optional[Machine] = None
_machine_lock = threading.Lock()


def machine() -> Machine:
    """The one sampler for the process (its readings depend on the last call)."""
    global _machine
    with _machine_lock:
        if _machine is None:
            _machine = Machine()
        return _machine
