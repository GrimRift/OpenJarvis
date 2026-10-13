"""The book voice: a second voice sidecar that only renders audiobooks.

User's choice (2026-10-13): a separate process, not the live sidecar, so a
book render that crashes or runs out of memory cannot take Sage's own voice
down, and the live Nano model never has to switch to the narrator's Turbo.
It runs only while the queue has work, at below-normal CPU priority.

Its cost is system memory, not just VRAM. The step-2 benchmark's first
attempt died loading Turbo with "The paging file is too small": a day-old
server had grown to 10.4 GB of commit. So before starting it, the commit
headroom -- what is free now plus what the pagefile may still grow by -- and
the free VRAM are checked, and the queue waits ("not enough memory") rather
than failing.
"""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any, Optional

import numpy as np

from openjarvis.core.paths import get_data_dir

logger = logging.getLogger(__name__)

PORT = 8792
START_TIMEOUT_SECONDS = 240.0
SYNTH_TIMEOUT_SECONDS = 180.0
#: Commit (GB) a render process needs to load, with room left for the live
#: server: a Turbo process peaked ~5 GB beside Sage on 10-13; Nano ~4.3 GB.
COMMIT_NEEDED_GB = {"turbo": 7.0, "nano": 5.5}
#: Free VRAM (GB) needed on the card: Turbo peaked 3.69 GB in the process
#: (card 6.23 GB with live Nano beside it); Nano ~2.2 GB.
VRAM_NEEDED_GB = {"turbo": 4.0, "nano": 2.4}


def voice_engine(voice: str) -> str:
    """'turbo' or 'nano' from the voice's meta.json (the sidecar's own rule)."""
    meta = Path(get_data_dir()) / "voices" / voice / "meta.json"
    try:
        return json.loads(meta.read_text(encoding="utf-8")).get("engine") or "nano"
    except (OSError, ValueError):
        return "nano"


def voice_exists(voice: str) -> bool:
    return (Path(get_data_dir()) / "voices" / voice / "reference.wav").is_file()


def commit_headroom_gb() -> Optional[float]:
    """GB that can still be committed: free now, plus what a pagefile with a
    larger maximum may still grow by. None where it cannot be measured."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        import winreg

        class _Status(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _Status()
        status.dwLength = ctypes.sizeof(_Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        gib = 1024**3
        free = status.ullAvailPageFile / gib
        current_pagefile = (status.ullTotalPageFile - status.ullTotalPhys) / gib
        growth = 0.0
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management",
        ) as key:
            entries, _ = winreg.QueryValueEx(key, "PagingFiles")
        maximum_mb = 0
        for entry in entries:
            parts = entry.split()
            if len(parts) >= 3 and parts[2].isdigit():
                maximum_mb += int(parts[2])
        if maximum_mb:
            growth = max(0.0, maximum_mb / 1024 - current_pagefile)
        return round(free + growth, 1)
    except Exception:
        logger.debug("commit headroom unavailable", exc_info=True)
        return None


def free_vram_gb() -> Optional[float]:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        return round(int(out.strip().splitlines()[0]) / 1024, 1)
    except Exception:
        return None


def resource_problem(voice: str) -> Optional[str]:
    """Why the book voice cannot start for *voice* now, or None."""
    kind = voice_engine(voice)
    headroom = commit_headroom_gb()
    if headroom is not None and headroom < COMMIT_NEEDED_GB[kind]:
        return (
            f"not enough memory: {headroom:.1f} GB can be committed, "
            f"{COMMIT_NEEDED_GB[kind]:.0f}+ needed (restarting Sage frees some)"
        )
    vram = free_vram_gb()
    if vram is not None and vram < VRAM_NEEDED_GB[kind]:
        return (
            f"not enough GPU memory: {vram:.1f} GB free, "
            f"{VRAM_NEEDED_GB[kind]:.1f} needed"
        )
    return None


class BookVoice:
    """Start, use and stop the render sidecar."""

    def __init__(self, port: int = PORT) -> None:
        self.port = port
        self._proc: Optional[subprocess.Popen] = None
        self.started_at = 0.0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _health(self) -> Optional[dict]:
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=2) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def start(self, voice: str) -> None:
        """Start it on *voice* and wait until it answers. Raises on failure."""
        from openjarvis.speech import chatterbox_sidecar as live

        if self.running():
            return
        speech_cfg: Any = None
        try:
            from openjarvis.core.config import load_config

            speech_cfg = getattr(load_config(), "speech", None)
        except Exception:
            pass
        exe = live.python_exe(speech_cfg)
        if not exe.exists():
            raise RuntimeError(f"voice environment missing: {exe}")
        root = live.repo_root()
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        log_dir = Path(get_data_dir()) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log = open(log_dir / "book_voice.log", "ab")
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0
            )
        self._proc = subprocess.Popen(
            [
                str(exe),
                "-m",
                "voice_sidecar",
                "--port",
                str(self.port),
                "--device",
                "cuda",
                "--voices-dir",
                str(live.voices_dir()),
                "--voice",
                voice,
                "--precision",
                "fp32",
            ],
            cwd=str(root),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )
        log.close()
        live._bind_lifetime_to_parent(self._proc)
        self.started_at = time.monotonic()
        logger.info("book voice starting (pid %s) on %s", self._proc.pid, voice)
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                code = self._proc.returncode
                self._proc = None
                raise RuntimeError(
                    f"book voice exited while loading (code {code}); "
                    "see logs/book_voice.log"
                )
            health = self._health()
            if health and health.get("model_loaded"):
                return
            time.sleep(1.0)
        self.stop()
        raise RuntimeError("book voice did not come up in time")

    def synthesize(self, text: str, voice: str) -> np.ndarray:
        body = json.dumps({"text": text, "voice": voice}).encode("utf-8")
        request = urllib.request.Request(
            self.url + "/synthesize",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=SYNTH_TIMEOUT_SECONDS) as r:
            data = r.read()
        with wave.open(io.BytesIO(data), "rb") as w:
            frames = w.readframes(w.getnframes())
            width = w.getsampwidth()
        if width == 4:
            return np.frombuffer(frames, dtype="<f4").astype(np.float32)
        return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        logger.info("book voice stopped")
