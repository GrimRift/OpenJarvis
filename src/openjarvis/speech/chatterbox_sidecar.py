"""Running the Chatterbox voice sidecar beside the server.

The sidecar is a separate process in its own Python environment (see
``scripts/setup_voice_sidecar.ps1``). This module starts it on demand, keeps
it running while Sage runs, and stops it with the server. It is resident
once started -- the user chose no idle unloading -- because a reload costs
seconds the first reply after a break would otherwise pay.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from openjarvis.core.paths import get_data_dir

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8791
HEALTH_TIMEOUT_SECONDS = 0.6
# First start downloads ~1 GB of weights; later starts load in seconds.
START_TIMEOUT_SECONDS = 180.0


def env_dir(speech_cfg: Any) -> Path:
    configured = str(getattr(speech_cfg, "chatterbox_env_dir", "") or "")
    return Path(configured) if configured else Path(get_data_dir()) / "voice-env"


def python_exe(speech_cfg: Any) -> Path:
    root = env_dir(speech_cfg)
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def port(speech_cfg: Any) -> int:
    return int(
        getattr(speech_cfg, "chatterbox_sidecar_port", DEFAULT_PORT) or DEFAULT_PORT
    )


def base_url(speech_cfg: Any) -> str:
    return f"http://127.0.0.1:{port(speech_cfg)}"


def ws_url(speech_cfg: Any) -> str:
    return f"ws://127.0.0.1:{port(speech_cfg)}/stream"


def voices_dir() -> Path:
    return Path(get_data_dir()) / "voices"


def repo_root() -> Path:
    # src/openjarvis/speech/this.py -> repo
    return Path(__file__).resolve().parents[3]


def fetch_health(
    speech_cfg: Any, timeout: float = HEALTH_TIMEOUT_SECONDS
) -> Optional[Dict]:
    """The sidecar's /health, or None when nothing answers."""
    try:
        with urllib.request.urlopen(
            base_url(speech_cfg) + "/health", timeout=timeout
        ) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def install_reason(speech_cfg: Any) -> Optional[str]:
    """Why the sidecar cannot be started, or None."""
    exe = python_exe(speech_cfg)
    if not exe.exists():
        return (
            f"voice sidecar environment not found at {env_dir(speech_cfg)} "
            "(run scripts/setup_voice_sidecar.ps1)"
        )
    if not (repo_root() / "voice_sidecar" / "server.py").exists():
        return "voice_sidecar package missing from the repository"
    return None


class SidecarProcess:
    """Owns at most one sidecar process for this server."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._starting = False
        self._last_error: Optional[str] = None
        self._log_path: Optional[Path] = None

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def ensure_started(self, speech_cfg: Any, *, wait: float = 0.0) -> Optional[Dict]:
        """Start the sidecar if it is not answering; optionally wait for it.

        Returns the health document when it is up, else None (``last_error``
        says why). Never raises: a missing sidecar is a fallback case, not a
        server failure.
        """
        health = fetch_health(speech_cfg)
        if health and health.get("ok"):
            return health
        reason = install_reason(speech_cfg)
        if reason:
            self._last_error = reason
            return None
        with self._lock:
            if not self.running() and not self._starting:
                self._starting = True
                try:
                    self._spawn(speech_cfg)
                except Exception as exc:
                    self._last_error = f"could not start voice sidecar: {exc}"
                    logger.warning(self._last_error)
                    self._starting = False
                    return None
        if wait <= 0:
            self._last_error = "voice sidecar is starting"
            return None
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            health = fetch_health(speech_cfg)
            if health and health.get("ok"):
                self._starting = False
                self._last_error = None
                return health
            if self._proc is not None and self._proc.poll() is not None:
                self._starting = False
                self._last_error = (
                    f"voice sidecar exited with code {self._proc.returncode}"
                    + (f" (see {self._log_path})" if self._log_path else "")
                )
                logger.warning(self._last_error)
                return None
            time.sleep(0.25)
        self._last_error = "voice sidecar is still loading"
        return None

    def _spawn(self, speech_cfg: Any) -> None:
        exe = python_exe(speech_cfg)
        root = repo_root()
        log_dir = Path(get_data_dir()) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = log_dir / "voice_sidecar.log"
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        args = [
            str(exe),
            "-m",
            "voice_sidecar",
            "--port",
            str(port(speech_cfg)),
            "--device",
            str(getattr(speech_cfg, "chatterbox_device", "cuda") or "cuda"),
            "--voices-dir",
            str(voices_dir()),
            "--voice",
            str(getattr(speech_cfg, "chatterbox_voice", "jarvis") or "jarvis"),
        ]
        creation = 0
        if os.name == "nt":
            # No console window of its own; it dies with the server below.
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        log = open(self._log_path, "ab")
        self._proc = subprocess.Popen(
            args,
            cwd=str(root),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creation,
        )
        logger.info(
            "voice sidecar started (pid %s), log at %s", self._proc.pid, self._log_path
        )
        _bind_lifetime_to_parent(self._proc)

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _bind_lifetime_to_parent(proc: subprocess.Popen) -> None:
    """On Windows, put the child in a Job Object that kills it when this
    process ends, so a crashed or force-closed server leaves no orphan
    holding the GPU."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
        JobObjectExtendedLimitInformation = 9
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        handle = wintypes.HANDLE(proc._handle)  # type: ignore[attr-defined]
        kernel32.AssignProcessToJobObject(job, handle)
        # Keep the job handle alive for the life of this process; closing it
        # is what kills the child.
        proc._sage_job_handle = job  # type: ignore[attr-defined]
    except Exception:
        logger.debug("could not bind sidecar lifetime to the server", exc_info=True)


_process = SidecarProcess()


def process() -> SidecarProcess:
    return _process


def start_if_selected(config: Any) -> None:
    """Called at server start: bring the sidecar up when it is the
    configured provider, without holding the server's startup."""
    speech_cfg = getattr(config, "speech", None)
    # The Settings choice (voice_choice.json), not only config.toml: after a
    # reboot the sidecar was not started because config still said
    # cartesia, so the first reply waited on a cold start and gave up.
    from openjarvis.speech.voice_choice import chosen_provider

    if chosen_provider(speech_cfg) != "chatterbox":
        return
    if install_reason(speech_cfg):
        logger.warning("Chatterbox selected but %s", install_reason(speech_cfg))
        return
    threading.Thread(
        target=_process.ensure_started,
        args=(speech_cfg,),
        kwargs={"wait": START_TIMEOUT_SECONDS},
        name="voice-sidecar-start",
        daemon=True,
    ).start()


def stop() -> None:
    _process.stop()


__all__ = [
    "START_TIMEOUT_SECONDS",
    "SidecarProcess",
    "base_url",
    "fetch_health",
    "install_reason",
    "process",
    "python_exe",
    "start_if_selected",
    "stop",
    "voices_dir",
    "ws_url",
]
