"""Shared diagnostic checks behind ``jarvis doctor``, the Health page and the
``system_health`` tool.

Five faults ran unreported for days each -- an expired Google token, three
APIs disabled at the project level, a briefing section that collected nothing,
a dashboard reading zero, a wake word firing on a muted microphone. Every one
was found by the user noticing. These checks exist so that asking is possible.
They are pull-only by deliberate decision: nothing here volunteers a warning.

Checks are split by cost. A default run reads only local state, so it is free
and instant. A check marked ``live`` makes an outbound call that may be
billable or quota-limited -- Routes and Places are capped at 30 requests a day
-- and runs only when explicitly requested.

Every surface must call :func:`run_health_checks` rather than assembling its
own list. This codebase's recurring failure is a second path that nobody
remembers to update, and an invariant test asserts the single path here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.config import DEFAULT_CONFIG_PATH, load_config

SECTION_SYSTEM = "system"
SECTION_VOICE = "voice"
SECTION_MODELS = "models"
SECTION_JOBS = "jobs"
SECTION_CREDENTIALS = "credentials"
SECTION_CONNECTORS = "connectors"
SECTION_FEATURES = "features"
SECTION_PROVIDERS = "providers"
SECTION_TOOLS = "tools"

SECTION_ORDER = (
    SECTION_SYSTEM,
    SECTION_VOICE,
    SECTION_MODELS,
    SECTION_JOBS,
    SECTION_CREDENTIALS,
    SECTION_CONNECTORS,
    SECTION_FEATURES,
    SECTION_PROVIDERS,
    SECTION_TOOLS,
)

SECTION_LABELS = {
    SECTION_SYSTEM: "System",
    SECTION_VOICE: "Voice pipeline",
    SECTION_MODELS: "Models and GPU",
    SECTION_JOBS: "Scheduled jobs",
    SECTION_CREDENTIALS: "Credentials",
    SECTION_CONNECTORS: "Connectors",
    SECTION_FEATURES: "Features",
    SECTION_PROVIDERS: "Providers",
    SECTION_TOOLS: "Tools",
}

# A refresh token older than this is worth mentioning: Google's unpublished
# OAuth apps expire them after seven days, and the failure is silent -- the
# briefing simply reports nothing having fetched nothing.
_REFRESH_TOKEN_STALE_DAYS = 7


@dataclass
class CheckResult:
    """Result of a single diagnostic check.

    ``fix`` names an operational remedy (re-run an OAuth flow, restart a
    stalled job) and never a source edit. Fixes are proposed, never applied
    without confirmation.
    """

    name: str
    status: str  # "ok", "warn", "fail"
    message: str
    details: Optional[str] = None
    section: str = SECTION_SYSTEM
    live: bool = False
    fix: Optional[str] = None


@dataclass
class HealthReport:
    """A full run: the checks, plus the worst status seen."""

    checks: List[CheckResult] = field(default_factory=list)
    live: bool = False

    @property
    def status(self) -> str:
        """Worst status across all checks."""
        for level in ("fail", "warn"):
            if any(c.status == level for c in self.checks):
                return level
        return "ok"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "live": self.live,
            "sections": [
                {
                    "id": section,
                    "label": SECTION_LABELS[section],
                    "checks": [asdict(c) for c in self.checks if c.section == section],
                }
                for section in SECTION_ORDER
                if any(c.section == section for c in self.checks)
            ],
        }

    def summarize(self) -> str:
        """One line per non-ok check, for speaking or chat."""
        problems = [c for c in self.checks if c.status != "ok"]
        if not problems:
            return f"All {len(self.checks)} checks passed."
        lines = [f"{len(problems)} of {len(self.checks)} checks need attention:"]
        for c in problems:
            lines.append(f"- {c.name}: {c.message}")
        return "\n".join(lines)


# -- Helpers -----------------------------------------------------------------


def _ensure_engines_imported() -> None:
    """Import engine modules to trigger registration decorators."""
    try:
        import openjarvis.engine  # noqa: F401
    except Exception:
        pass


def _get_config() -> Any:
    """Load config or return a default if parsing fails."""
    try:
        return load_config()
    except Exception:
        from openjarvis.core.config import JarvisConfig

        return JarvisConfig()


def _config_dir() -> Path:
    return DEFAULT_CONFIG_PATH.parent


# -- System ------------------------------------------------------------------


def _check_python_version() -> CheckResult:
    """Check that Python version is >= 3.10."""
    ver = sys.version_info
    version_str = f"{ver.major}.{ver.minor}.{ver.micro}"
    if (ver.major, ver.minor) >= (3, 10):
        return CheckResult("Python version", "ok", version_str)
    return CheckResult("Python version", "fail", f"{version_str} (requires >= 3.10)")


def _check_config_exists() -> CheckResult:
    """Check that the config file exists."""
    if DEFAULT_CONFIG_PATH.exists():
        return CheckResult("Config file", "ok", str(DEFAULT_CONFIG_PATH))
    return CheckResult(
        "Config file",
        "warn",
        f"Not found at {DEFAULT_CONFIG_PATH}",
        details="Run `jarvis init` to generate a config file.",
    )


def _check_config_parses() -> CheckResult:
    """Check that the config file parses successfully."""
    if not DEFAULT_CONFIG_PATH.exists():
        return CheckResult("Config parsing", "warn", "Skipped (no config file)")
    try:
        load_config()
        return CheckResult("Config parsing", "ok", "Config loaded successfully")
    except Exception as exc:
        return CheckResult("Config parsing", "fail", f"Parse error: {exc}")


def _check_security_profile() -> CheckResult:
    """Check if a security profile is configured."""
    try:
        config = load_config()
        if config.security.profile:
            return CheckResult(
                name="Security profile",
                status="ok",
                message=f"Profile '{config.security.profile}' active",
            )
        return CheckResult(
            name="Security profile",
            status="warn",
            message="No security profile set",
            details="Recommended: add security.profile = 'personal' to config.toml",
        )
    except Exception as exc:
        return CheckResult(
            name="Security profile",
            status="fail",
            message=f"Could not check: {exc}",
        )


def _check_nodejs() -> CheckResult:
    """Check Node.js version for Node-backed integrations."""
    node_path = shutil.which("node")
    if not node_path:
        return CheckResult(
            "Node.js",
            "warn",
            "Not found",
            details=(
                "Node.js 22+ is required for ClaudeCodeAgent and the "
                "WhatsApp Baileys channel bridge."
            ),
        )
    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        version_str = result.stdout.strip()
        parts = version_str.lstrip("v").split(".")
        major = int(parts[0])
        if major >= 22:
            return CheckResult("Node.js", "ok", version_str)
        return CheckResult(
            "Node.js",
            "warn",
            f"{version_str} (requires >= v22)",
            details=(
                "Upgrade Node.js for ClaudeCodeAgent and WhatsApp Baileys support."
            ),
        )
    except Exception as exc:
        return CheckResult("Node.js", "warn", f"Error checking version: {exc}")


def _check_optional_deps() -> List[CheckResult]:
    """Check optional dependencies that this machine could actually use.

    Warning that Apple Silicon energy monitoring is missing on a Windows box
    is noise, and noise is what makes a health page get ignored. Packages
    that cannot apply here are reported as not applicable, not as warnings.
    """
    results: List[CheckResult] = []
    has_nvidia = shutil.which("nvidia-smi") is not None
    is_mac = sys.platform == "darwin"

    optional_packages = [
        ("fastapi", "openjarvis[server]", "REST API server", True),
        ("torch", "pip install torch", "SFT/GRPO training", True),
        ("pynvml", "openjarvis[gpu-metrics]", "NVIDIA energy monitoring", has_nvidia),
        ("amdsmi", "openjarvis[energy-amd]", "AMD energy monitoring", not has_nvidia),
        ("colbert", "openjarvis[memory-colbert]", "ColBERT memory backend", True),
        ("zeus", "openjarvis[energy-apple]", "Apple Silicon energy monitoring", is_mac),
    ]
    for pkg, install_hint, description, applicable in optional_packages:
        if not applicable:
            results.append(
                CheckResult(
                    f"Optional: {description}",
                    "ok",
                    "Not applicable on this machine",
                )
            )
            continue
        try:
            __import__(pkg)
            results.append(CheckResult(f"Optional: {description}", "ok", "Installed"))
        except Exception:
            results.append(
                CheckResult(
                    f"Optional: {description}",
                    "warn",
                    f"Not installed ({install_hint})",
                )
            )
    return results


# -- Voice -------------------------------------------------------------------


def _check_speech_backend() -> CheckResult:
    """Check whether the configured speech backend can load."""
    try:
        from openjarvis.speech._discovery import get_speech_backend

        config = _get_config()
        backend = get_speech_backend(config)
        if backend is None:
            return CheckResult(
                "Speech backend",
                "warn",
                "Not configured",
                details="Install desktop dependencies with `uv sync --extra desktop`.",
                section=SECTION_VOICE,
            )

        if backend.health():
            return CheckResult(
                "Speech backend",
                "ok",
                f"{backend.backend_id} ready",
                section=SECTION_VOICE,
            )

        details = None
        last_error = getattr(backend, "last_error", None)
        if callable(last_error):
            details = last_error()
        return CheckResult(
            "Speech backend",
            "warn",
            f"{backend.backend_id} unavailable",
            details=details
            or "Install desktop dependencies with `uv sync --extra desktop`.",
            section=SECTION_VOICE,
        )
    except Exception as exc:
        return CheckResult(
            "Speech backend",
            "warn",
            f"Could not check: {exc}",
            section=SECTION_VOICE,
        )


def _check_wake_word() -> CheckResult:
    """Check that the wake-word model file the config points at exists.

    The wake word once fired on a muted microphone, so a present model is
    necessary but not sufficient; this reports the file, not the audio path.
    """
    config = _get_config()
    speech_cfg = getattr(config, "speech", None)
    model_path = getattr(speech_cfg, "wake_word_model", None) if speech_cfg else None
    if not model_path:
        return CheckResult(
            "Wake word model",
            "warn",
            "No wake-word model configured",
            section=SECTION_VOICE,
        )
    path = Path(str(model_path))
    if path.exists():
        size_kb = path.stat().st_size // 1024
        return CheckResult(
            "Wake word model",
            "ok",
            f"{path.name} ({size_kb} kB)",
            section=SECTION_VOICE,
        )
    return CheckResult(
        "Wake word model",
        "fail",
        f"Configured model is missing: {path}",
        details="The wake word cannot trigger until this file exists.",
        section=SECTION_VOICE,
    )


def _check_tts_credentials() -> CheckResult:
    """Report whether a speech synthesis key is present -- never its value."""
    config = _get_config()
    speech_cfg = getattr(config, "speech", None)
    provider = getattr(speech_cfg, "tts_provider", None) if speech_cfg else None
    env_present = bool(
        _provider_key("CARTESIA_API_KEY") or _provider_key("OPENJARVIS_TTS_API_KEY")
    )
    if env_present:
        return CheckResult(
            "Speech synthesis key",
            "ok",
            f"Key present for {provider or 'the configured provider'}",
            section=SECTION_VOICE,
        )
    return CheckResult(
        "Speech synthesis key",
        "warn",
        "No synthesis key in the environment",
        details=(
            "Sage will fall back to a local voice. Set the provider key in the "
            "Sage user environment and restart through the Start menu shortcut."
        ),
        section=SECTION_VOICE,
    )


def _check_flux_streaming() -> CheckResult:
    """Whether streaming speech-to-text could run, and why not if it cannot.

    Reuses the reason the Settings toggle already shows, so the Health page
    and the toggle cannot disagree about why Flux is unavailable. The key
    itself is never included, only whether one is present.
    """
    try:
        from openjarvis.server.flux_routes import _unavailable_reason
    except Exception:
        return CheckResult(
            "Streaming speech-to-text",
            "warn",
            "Flux support is not installed",
            section=SECTION_VOICE,
        )

    config = _get_config()
    speech_cfg = getattr(config, "speech", None)
    # The key may live in credentials.toml and only reach the environment
    # when the server injects it, so a plain shell sees nothing while the
    # running server is fine.
    has_key = bool(_provider_key("DEEPGRAM_API_KEY"))
    enabled = bool(getattr(speech_cfg, "flux_enabled", True))
    try:
        import websockets  # noqa: F401

        deps = True
    except ImportError:
        deps = False

    # Deliberately not flux.is_available(): that reads os.environ only, so it
    # reports a correctly configured key as missing whenever this runs outside
    # the server process.
    available = has_key and enabled and deps

    if not available and has_key and enabled and not deps:
        return CheckResult(
            "Streaming speech-to-text",
            "warn",
            "Flux dependencies are not installed",
            details="Speech falls back to the local backend.",
            section=SECTION_VOICE,
        )

    if available:
        return CheckResult(
            "Streaming speech-to-text",
            "ok",
            "Flux can connect",
            section=SECTION_VOICE,
        )

    reason = ""
    try:
        reason = _unavailable_reason(speech_cfg)
    except Exception:
        reason = "unavailable"
    return CheckResult(
        "Streaming speech-to-text",
        "warn",
        reason,
        details="Speech falls back to the local backend.",
        section=SECTION_VOICE,
    )


def _check_speech_device() -> CheckResult:
    """The speech backend must have the device it was configured for.

    ``device = "cuda"`` with no usable CUDA silently falls back to CPU:
    transcription still works, several times slower, and nothing says so.

    Which runtime to ask is not obvious and getting it wrong is worse than
    not checking. faster-whisper runs on ctranslate2, not torch; this machine
    has a CPU-only torch build alongside a ctranslate2 that sees the GPU
    perfectly well, so asking ``torch.cuda`` reported a healthy voice
    pipeline as broken.
    """
    config = _get_config()
    speech_cfg = getattr(config, "speech", None)
    configured = str(getattr(speech_cfg, "device", "") or "auto").lower()
    backend = str(getattr(speech_cfg, "backend", "") or "auto").lower()

    if configured in ("", "auto", "cpu"):
        return CheckResult(
            "Speech device",
            "ok",
            f"Configured as {configured or 'auto'}",
            section=SECTION_VOICE,
        )

    runtime = "ctranslate2" if backend in ("auto", "faster-whisper") else "torch"
    available: Optional[bool] = None
    error = ""
    if runtime == "ctranslate2":
        try:
            import ctranslate2

            available = ctranslate2.get_cuda_device_count() > 0
        except Exception as exc:
            error = str(exc)
    else:
        try:
            import torch

            available = bool(torch.cuda.is_available())
        except Exception as exc:
            error = str(exc)

    if available is None:
        return CheckResult(
            "Speech device",
            "warn",
            f"Configured for {configured}, cannot confirm via {runtime}: {error}",
            section=SECTION_VOICE,
        )
    if available:
        return CheckResult(
            "Speech device",
            "ok",
            f"{configured} available to {runtime}",
            section=SECTION_VOICE,
        )
    return CheckResult(
        "Speech device",
        "fail",
        f"Configured for {configured}, which {runtime} cannot use",
        details=(
            "Transcription falls back to CPU and runs several times slower "
            "without reporting anything."
        ),
        section=SECTION_VOICE,
    )


# -- Models and GPU ----------------------------------------------------------


def _configured_engine_keys(config: Any) -> set:
    """Engine keys this config actually names.

    Ten engines are registered and two are in use. Warning about the eight
    that were never configured trains the reader to ignore the page, so the
    rest are reported as configured-off rather than broken.
    """
    keys = set()
    engine_section = getattr(config, "engine", None)
    default = getattr(engine_section, "default", None)
    if default:
        keys.add(str(default))
    for attr in dir(config):
        if attr.startswith("_"):
            continue
        try:
            section = getattr(config, attr)
        except Exception:
            continue
        name = getattr(section, "engine", None)
        if isinstance(name, str) and name:
            keys.add(name)
    return keys


def _check_engines(live: bool = False) -> List[CheckResult]:
    """Probe each registered engine for health.

    A cloud engine's health probe is an outbound call to a paid API, so it is
    held back for a live run; local engines answer over localhost for free.
    """
    results: List[CheckResult] = []

    _ensure_engines_imported()

    from openjarvis.core.registry import EngineRegistry
    from openjarvis.engine import _discovery

    config = _get_config()
    configured = _configured_engine_keys(config)

    for key in sorted(EngineRegistry.keys()):
        if key not in configured:
            results.append(
                CheckResult(
                    f"Engine: {key}",
                    "ok",
                    "Not configured",
                    section=SECTION_MODELS,
                )
            )
            continue
        try:
            engine = _discovery._make_engine(key, config)
        except Exception as exc:
            results.append(
                CheckResult(
                    f"Engine: {key}",
                    "warn",
                    f"Unreachable ({exc})",
                    section=SECTION_MODELS,
                )
            )
            continue

        if getattr(engine, "is_cloud", False) and not live:
            results.append(
                CheckResult(
                    f"Engine: {key}",
                    "ok",
                    "Configured (not probed)",
                    details="Run a live check to reach this provider.",
                    section=SECTION_MODELS,
                )
            )
            continue

        try:
            if engine.health():
                results.append(
                    CheckResult(
                        f"Engine: {key}",
                        "ok",
                        "Reachable",
                        section=SECTION_MODELS,
                        live=bool(getattr(engine, "is_cloud", False)),
                    )
                )
            else:
                results.append(
                    CheckResult(
                        f"Engine: {key}",
                        "warn",
                        "Unreachable",
                        section=SECTION_MODELS,
                        live=bool(getattr(engine, "is_cloud", False)),
                    )
                )
        except Exception as exc:
            results.append(
                CheckResult(
                    f"Engine: {key}",
                    "warn",
                    f"Unreachable ({exc})",
                    section=SECTION_MODELS,
                )
            )

    if not results:
        results.append(
            CheckResult(
                "Engines", "warn", "No engines registered", section=SECTION_MODELS
            )
        )

    return results


def _check_default_model(live: bool = False) -> CheckResult:
    """Check whether the configured default model is available."""
    try:
        config = load_config()
    except Exception:
        return CheckResult(
            "Default model",
            "warn",
            "Skipped (config unavailable)",
            section=SECTION_MODELS,
        )

    default_model = config.intelligence.default_model
    if not default_model:
        return CheckResult(
            "Default model",
            "ok",
            "Not configured (auto-routing enabled)",
            details="Router will select a model dynamically.",
            section=SECTION_MODELS,
        )

    _ensure_engines_imported()

    from openjarvis.core.registry import EngineRegistry
    from openjarvis.engine import _discovery

    preferred = config.intelligence.preferred_engine or config.engine.default
    check_order = []
    if preferred:
        check_order.append(preferred)
    check_order += [k for k in sorted(EngineRegistry.keys()) if k != preferred]

    for key in check_order:
        try:
            engine = _discovery._make_engine(key, config)
            if getattr(engine, "is_cloud", False) and not live:
                continue
            if engine.health():
                models = engine.list_models()
                if default_model in models:
                    return CheckResult(
                        "Default model",
                        "ok",
                        f"{default_model} (on {key})",
                        section=SECTION_MODELS,
                    )
        except Exception:
            continue

    return CheckResult(
        "Default model",
        "warn",
        f"{default_model} not found on any reachable engine",
        section=SECTION_MODELS,
    )


def _check_models(live: bool = False) -> List[CheckResult]:
    """List models from healthy engines."""
    results: List[CheckResult] = []

    _ensure_engines_imported()

    from openjarvis.core.registry import EngineRegistry
    from openjarvis.engine import _discovery

    config = _get_config()
    configured = _configured_engine_keys(config)

    for key in sorted(EngineRegistry.keys()):
        if key not in configured:
            continue
        try:
            engine = _discovery._make_engine(key, config)
            if getattr(engine, "is_cloud", False) and not live:
                continue
            if engine.health():
                models = engine.list_models()
                if models:
                    model_list = ", ".join(models[:5])
                    suffix = f" (+{len(models) - 5} more)" if len(models) > 5 else ""
                    results.append(
                        CheckResult(
                            f"Models: {key}",
                            "ok",
                            f"{model_list}{suffix}",
                            section=SECTION_MODELS,
                        )
                    )
                else:
                    results.append(
                        CheckResult(
                            f"Models: {key}",
                            "warn",
                            "No models available",
                            details="Pull a model (e.g. `ollama pull qwen3.5:2b`).",
                            section=SECTION_MODELS,
                        )
                    )
        except Exception:
            continue

    return results


def _check_gpu() -> CheckResult:
    """Report the GPU, because the speech backend is configured onto CUDA.

    ``nvidia-smi`` is a local process, not a network call, so this stays in
    the free default run.
    """
    config = _get_config()
    speech_cfg = getattr(config, "speech", None)
    wants_cuda = str(getattr(speech_cfg, "device", "") or "").lower() == "cuda"

    smi = shutil.which("nvidia-smi")
    if not smi:
        status = "warn" if wants_cuda else "ok"
        message = "No NVIDIA GPU detected"
        details = (
            "Speech is configured for CUDA but no GPU driver was found; "
            "transcription will fall back to CPU and be slower."
            if wants_cuda
            else None
        )
        return CheckResult("GPU", status, message, details, section=SECTION_MODELS)

    try:
        result = subprocess.run(
            [
                smi,
                "--query-gpu=name,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        line = result.stdout.strip().splitlines()[0]
        name, used, total = (part.strip() for part in line.split(","))
        used_mb, total_mb = int(used), int(total)
        pct = (used_mb * 100) // total_mb if total_mb else 0
        status = "warn" if pct >= 90 else "ok"
        return CheckResult(
            "GPU",
            status,
            f"{name}, {used_mb}/{total_mb} MiB used ({pct}%)",
            details=(
                "Memory is nearly full; a model load may fail." if status == "warn"
                else None
            ),
            section=SECTION_MODELS,
        )
    except Exception as exc:
        return CheckResult(
            "GPU", "warn", f"Could not read GPU state: {exc}", section=SECTION_MODELS
        )


# -- Scheduled jobs ----------------------------------------------------------


def _check_scheduled_jobs() -> List[CheckResult]:
    """Report active jobs and whether each one's last run succeeded.

    A scheduled task that fails every night is exactly the class of fault
    that went unreported here, so a failed last run is a ``fail``, not a
    note. Cron is evaluated in UTC while this machine is UTC+8, which is why
    the schedule is shown verbatim rather than reinterpreted.

    The run log records ``success`` as 0/1 and the task carries no name, so
    the prompt is the only human label available.
    """
    results: List[CheckResult] = []
    db_path = _config_dir() / "scheduler.db"
    if not db_path.exists():
        return [
            CheckResult(
                "Scheduled jobs",
                "ok",
                "No scheduler database yet",
                section=SECTION_JOBS,
            )
        ]

    try:
        from openjarvis.scheduler.store import SchedulerStore

        store = SchedulerStore(db_path)
        try:
            tasks = store.list_tasks()
            active = [t for t in tasks if str(t.get("status")) == "active"]
            results.append(
                CheckResult(
                    "Scheduled jobs",
                    "ok",
                    f"{len(active)} active, {len(tasks) - len(active)} inactive",
                    section=SECTION_JOBS,
                )
            )

            for task in active:
                task_id = str(task.get("id") or "?")
                prompt = str(task.get("prompt") or task_id).strip()
                label = prompt[:60] + ("..." if len(prompt) > 60 else "")
                schedule = (
                    f"{task.get('schedule_type', '')} "
                    f"{task.get('schedule_value', '')}"
                ).strip()

                try:
                    logs = store.get_run_logs(task_id, limit=1)
                except Exception:
                    logs = []

                if not logs:
                    results.append(
                        CheckResult(
                            f"Job: {label}",
                            "warn",
                            "Has never run",
                            details=f"Schedule: {schedule}" if schedule else None,
                            section=SECTION_JOBS,
                        )
                    )
                    continue

                last = logs[0]
                when = str(last.get("started_at") or last.get("finished_at") or "")
                if last.get("success"):
                    results.append(
                        CheckResult(
                            f"Job: {label}",
                            "ok",
                            f"Last run succeeded {when}".strip(),
                            section=SECTION_JOBS,
                        )
                    )
                else:
                    results.append(
                        CheckResult(
                            f"Job: {label}",
                            "fail",
                            f"Last run failed {when}".strip(),
                            details=str(last.get("error") or "")[:400] or None,
                            section=SECTION_JOBS,
                            fix=f"rerun-job:{task_id}",
                        )
                    )
        finally:
            store.close()
    except Exception as exc:
        results.append(
            CheckResult(
                "Scheduled jobs",
                "warn",
                f"Could not read the scheduler: {exc}",
                section=SECTION_JOBS,
            )
        )
    return results


# -- Credentials and connectors ----------------------------------------------


def _token_file_report(path: Path) -> CheckResult:
    """Report one connector's stored credential by shape, never by value.

    Connectors do not share a shape: OAuth files carry a refresh token, the
    weather connector carries an API key, and the Obsidian one carries a
    vault path. Applying the OAuth rule to all of them reported three
    healthy connectors as broken, so the shape decides which rule applies.

    For the OAuth ones, ``expires_in`` is a duration and no absolute expiry
    is stored, so the only age signal is the file's own mtime. What actually
    decides whether access survives is the presence of a refresh token.
    """
    name = path.stem
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return CheckResult(
            f"Credential: {name}",
            "fail",
            f"Unreadable credential file ({exc})",
            section=SECTION_CREDENTIALS,
            fix=f"reauth:{name}",
        )

    if not isinstance(data, dict):
        return CheckResult(
            f"Credential: {name}",
            "warn",
            "Unexpected credential file shape",
            section=SECTION_CREDENTIALS,
        )

    age_days = int((time.time() - path.stat().st_mtime) / 86400)
    is_oauth = bool(
        data.get("refresh_token")
        or data.get("access_token")
        or data.get("token")
    )

    if is_oauth:
        if not data.get("refresh_token"):
            return CheckResult(
                f"Credential: {name}",
                "fail",
                "Authorized without a refresh token",
                details=(
                    "Access stops at the next expiry and cannot renew itself. "
                    "Re-run this connector's authorization flow."
                ),
                section=SECTION_CREDENTIALS,
                fix=f"reauth:{name}",
            )
        if age_days > _REFRESH_TOKEN_STALE_DAYS:
            return CheckResult(
                f"Credential: {name}",
                "warn",
                f"Refreshed {age_days} days ago",
                details=(
                    "An unpublished Google OAuth app expires refresh tokens "
                    "after seven days, and the failure is silent. If this "
                    "connector has stopped returning data, re-authorize it."
                ),
                section=SECTION_CREDENTIALS,
                fix=f"reauth:{name}",
            )
        return CheckResult(
            f"Credential: {name}",
            "ok",
            f"OAuth token refreshed {age_days} days ago",
            section=SECTION_CREDENTIALS,
        )

    if data.get("api_key"):
        return CheckResult(
            f"Credential: {name}",
            "ok",
            "API key present",
            section=SECTION_CREDENTIALS,
        )

    if data.get("client_id") or data.get("client_secret"):
        return CheckResult(
            f"Credential: {name}",
            "warn",
            "Registered but never authorized",
            details=(
                "The app credentials are stored but no token was ever "
                "granted, so this connector cannot return anything yet."
            ),
            section=SECTION_CREDENTIALS,
            fix=f"reauth:{name}",
        )

    return CheckResult(
        f"Credential: {name}",
        "ok",
        "Local configuration, no credential needed",
        section=SECTION_CREDENTIALS,
    )


def _check_credentials() -> List[CheckResult]:
    """Report every stored connector token by presence and age."""
    results: List[CheckResult] = []
    connector_dir = _config_dir() / "connectors"
    if not connector_dir.is_dir():
        return [
            CheckResult(
                "Connector tokens",
                "warn",
                "No connector directory",
                section=SECTION_CREDENTIALS,
            )
        ]

    token_files = sorted(
        p for p in connector_dir.glob("*.json") if not p.name.startswith("_")
    )
    if not token_files:
        return [
            CheckResult(
                "Connector tokens",
                "warn",
                "No connectors authorized",
                section=SECTION_CREDENTIALS,
            )
        ]

    for path in token_files:
        results.append(_token_file_report(path))
    return results


def _check_connectors() -> List[CheckResult]:
    """Compare the connectors that are switched on against the tokens present.

    A connector enabled with no token is the shape of the fault where the
    briefing's ``world`` section was configured on and collected nothing.
    """
    results: List[CheckResult] = []
    connector_dir = _config_dir() / "connectors"
    activated_path = connector_dir / "_activated.json"

    if not activated_path.exists():
        return [
            CheckResult(
                "Activated connectors",
                "ok",
                "No activation list",
                section=SECTION_CONNECTORS,
            )
        ]

    try:
        data = json.loads(activated_path.read_text(encoding="utf-8"))
        sources = data.get("sources") or []
    except Exception as exc:
        return [
            CheckResult(
                "Activated connectors",
                "warn",
                f"Could not read the activation list: {exc}",
                section=SECTION_CONNECTORS,
            )
        ]

    if not sources:
        return [
            CheckResult(
                "Activated connectors",
                "ok",
                "None activated",
                section=SECTION_CONNECTORS,
            )
        ]

    for source in sorted(str(s) for s in sources):
        token_path = connector_dir / f"{source}.json"
        if token_path.exists():
            results.append(
                CheckResult(
                    f"Connector: {source}",
                    "ok",
                    "Activated with a stored token",
                    section=SECTION_CONNECTORS,
                )
            )
        else:
            results.append(
                CheckResult(
                    f"Connector: {source}",
                    "fail",
                    "Activated but has no token",
                    details=(
                        "This connector is switched on and cannot return "
                        "anything. Authorize it or switch it off."
                    ),
                    section=SECTION_CONNECTORS,
                    fix=f"reauth:{source}",
                )
            )
    return results


# -- Features ----------------------------------------------------------------


def _check_digest_sections() -> List[CheckResult]:
    """Every enabled briefing section must resolve to at least one source.

    The ``world`` section -- weather, Hacker News, RSS -- was enabled in config
    and collected nothing for the entire life of the feature, because an empty
    configured list was read as "collect nothing" rather than "not
    configured". Nothing errored; the only trace was ``sources_used`` missing
    one section. That resolution bug is fixed, but a section can still resolve
    to nothing through a typo or a new section with no default, and it would
    fail exactly as silently.
    """
    config = _get_config()
    digest = getattr(config, "digest", None)
    if digest is None or not getattr(digest, "enabled", False):
        return [
            CheckResult(
                "Briefing sections",
                "ok",
                "The morning briefing is disabled",
                section=SECTION_FEATURES,
            )
        ]

    try:
        from openjarvis.agents.morning_digest import (
            _BROWSER_SOURCES,
            DEFAULT_SECTION_SOURCES,
        )
    except Exception as exc:
        return [
            CheckResult(
                "Briefing sections",
                "warn",
                f"Could not read the briefing's sources: {exc}",
                section=SECTION_FEATURES,
            )
        ]

    # Outlook and Teams have no connector: they are scraped through the
    # browser by _collect_browser_sources, so they legitimately resolve to no
    # connector sources. Counting them as silent reported a working section as
    # broken on the first run of this check.
    browser_backed = {section for section, _tool, _label in _BROWSER_SOURCES}

    sections = list(getattr(digest, "sections", []) or [])
    silent: List[str] = []
    for section in sections:
        name = str(section).strip()
        if not name or name in browser_backed:
            continue
        configured = getattr(getattr(digest, name, None), "sources", None)
        if not list(configured or DEFAULT_SECTION_SOURCES.get(name, [])):
            silent.append(name)

    if silent:
        return [
            CheckResult(
                "Briefing sections",
                "fail",
                f"Enabled but collect nothing: {', '.join(sorted(silent))}",
                details=(
                    "These sections are switched on and resolve to no "
                    "sources, so the briefing asks for nothing and reports "
                    "nothing. Give each one sources, or remove it from "
                    "[digest] sections."
                ),
                section=SECTION_FEATURES,
            )
        ]
    return [
        CheckResult(
            "Briefing sections",
            "ok",
            f"{len(sections)} sections all resolve to sources",
            section=SECTION_FEATURES,
        )
    ]


def _check_scheduler_running() -> CheckResult:
    """The scheduler's poll loop must actually be alive.

    The job checks report each task's last run, which says nothing about
    whether anything is still due to fire. A dead poll thread leaves every job
    looking healthy while none of them will ever run again.
    """
    try:
        from openjarvis.scheduler.tools import ListScheduledTasksTool

        scheduler = getattr(ListScheduledTasksTool, "_scheduler", None)
    except Exception as exc:
        return CheckResult(
            "Scheduler",
            "warn",
            f"Could not reach the scheduler: {exc}",
            section=SECTION_FEATURES,
        )

    if scheduler is None:
        return CheckResult(
            "Scheduler",
            "warn",
            "Not running in this process",
            details=(
                "Expected when the check runs from the CLI; the same line in "
                "a report from the running server means the scheduler never "
                "started."
            ),
            section=SECTION_FEATURES,
        )

    thread = getattr(scheduler, "_thread", None)
    if thread is None or not thread.is_alive():
        return CheckResult(
            "Scheduler",
            "fail",
            "The scheduler is not polling",
            details=(
                "Scheduled jobs will not fire, however healthy their last "
                "runs look. Restart Sage."
            ),
            section=SECTION_FEATURES,
        )
    return CheckResult(
        "Scheduler", "ok", "Polling for due jobs", section=SECTION_FEATURES
    )


def _check_telemetry_recording(app_state: Any = None) -> List[CheckResult]:
    """Every engine that can answer must be wrapped for telemetry.

    The dashboard read zero for energy, tokens and requests through a whole
    working day. Telemetry was enabled, the store subscribed and the database
    held thousands of rows -- but only the primary engine was wrapped in
    ``InstrumentedEngine`` while discovered and cloud engines went into
    ``MultiEngine`` raw, and the chat default is a cloud model. No amount of
    reading config shows this: every value is correct while nothing records.
    """
    if app_state is None:
        return []

    engine = getattr(app_state, "engine", None)
    if engine is None:
        return [
            CheckResult(
                "Telemetry instrumentation",
                "warn",
                "No engine on the server",
                section=SECTION_FEATURES,
            )
        ]

    try:
        from openjarvis.telemetry.instrumented_engine import InstrumentedEngine
    except Exception as exc:
        return [
            CheckResult(
                "Telemetry instrumentation",
                "warn",
                f"Telemetry support is unavailable: {exc}",
                section=SECTION_FEATURES,
            )
        ]

    entries = getattr(engine, "_engines", None)
    pairs = list(entries) if entries else [("default", engine)]
    unwrapped = [
        str(name) for name, inner in pairs if not isinstance(inner, InstrumentedEngine)
    ]

    if unwrapped:
        return [
            CheckResult(
                "Telemetry instrumentation",
                "fail",
                f"Not recording: {', '.join(sorted(unwrapped))}",
                details=(
                    "Calls routed to these engines are invisible to the "
                    "dashboard, which will read low or zero while the machine "
                    "is busy."
                ),
                section=SECTION_FEATURES,
            )
        ]
    return [
        CheckResult(
            "Telemetry instrumentation",
            "ok",
            f"All {len(pairs)} engines recording",
            section=SECTION_FEATURES,
        )
    ]


def _check_presence(app_state: Any = None) -> List[CheckResult]:
    """What Sage believes about whether anyone is at the desk, and why.

    Shown before any moment can fire on it: if this line is wrong, every
    unprompted word built on it is wrong too, and the place to find that out
    is here rather than by being greeted in an empty room.
    """
    if app_state is None:
        return []
    monitor = getattr(app_state, "presence_monitor", None)
    if monitor is None:
        return [
            CheckResult(
                "Presence",
                "warn",
                "Monitor not running",
                section=SECTION_FEATURES,
            )
        ]
    try:
        snap = monitor.snapshot()
    except Exception as exc:
        return [
            CheckResult(
                "Presence",
                "warn",
                f"Could not read: {exc}",
                section=SECTION_FEATURES,
            )
        ]

    if snap.state == "disabled":
        return [
            CheckResult(
                "Presence",
                "ok",
                "Switched off",
                details="Turn it on in Settings for Sage to know when you are here.",
                section=SECTION_FEATURES,
            )
        ]
    if snap.state == "unknown":
        return [
            CheckResult(
                "Presence",
                "warn",
                "Cannot tell",
                details=snap.reason or None,
                section=SECTION_FEATURES,
            )
        ]
    if not monitor.running():
        return [
            CheckResult(
                "Presence",
                "fail",
                "Monitor thread has stopped",
                details="Presence will be stale until Sage is restarted.",
                section=SECTION_FEATURES,
            )
        ]
    where = f", in front: {snap.foreground}" if snap.foreground else ""
    return [
        CheckResult(
            "Presence",
            "ok",
            f"{snap.state} ({snap.reason}{where})",
            section=SECTION_FEATURES,
        )
    ]


# -- Providers ---------------------------------------------------------------

# One minimal read per Google API. Presence of a credential file proves
# nothing: Drive, Contacts and Tasks returned 403 for months with perfect
# tokens because the APIs were disabled at the project level, and that was
# misdiagnosed as a scope problem for just as long. Only a real call
# distinguishes "not authorized" from "not enabled".
_GOOGLE_PROBES = {
    "gmail": "https://gmail.googleapis.com/gmail/v1/users/me/labels",
    "gcalendar": "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1",
    "google_tasks": "https://tasks.googleapis.com/tasks/v1/users/@me/lists?maxResults=1",
    "gdrive": "https://www.googleapis.com/drive/v3/files?pageSize=1",
    "gcontacts": "https://people.googleapis.com/v1/people/me/connections?pageSize=1&personFields=names",
}

_PROVIDER_TIMEOUT = 15.0


def _provider_key(name: str) -> str:
    """Read a provider key the way the server does.

    The server injects ``credentials.toml`` into the environment at startup,
    so a key can be correctly configured and still absent from a plain shell.
    Reading only ``os.environ`` made this check report a working Deepgram key
    as missing whenever it ran outside the server -- the same
    configuration-versus-reality gap these checks exist to close, pointing the
    wrong way.
    """
    value = os.environ.get(name, "")
    if value:
        return value
    try:
        from openjarvis.core.credentials import load_credentials

        for _tool, kvs in load_credentials().items():
            if kvs.get(name):
                return str(kvs[name])
    except Exception:
        return ""
    return ""


def _check_google_apis(live: bool) -> List[CheckResult]:
    """Refresh each Google token and make one minimal call with it.

    The refresh is half the value: an unpublished OAuth app expires refresh
    tokens after seven days, and the only symptom was a briefing that
    reported nothing having fetched nothing.
    """
    connector_dir = _config_dir() / "connectors"
    present = [
        name for name in _GOOGLE_PROBES if (connector_dir / f"{name}.json").exists()
    ]
    if not present:
        return []

    if not live:
        return [
            CheckResult(
                "Google APIs",
                "ok",
                f"{len(present)} configured (not called)",
                details="Run a live check to find a disabled API or a dead token.",
                section=SECTION_PROVIDERS,
            )
        ]

    try:
        import httpx

        from openjarvis.connectors.oauth import refresh_google_token
    except Exception as exc:
        return [
            CheckResult(
                "Google APIs",
                "warn",
                f"Cannot probe: {exc}",
                section=SECTION_PROVIDERS,
            )
        ]

    results: List[CheckResult] = []
    for name in sorted(present):
        path = str(connector_dir / f"{name}.json")
        try:
            token = refresh_google_token(path)
        except Exception as exc:
            token = None
            refresh_error: Optional[str] = str(exc)
        else:
            refresh_error = None

        if not token:
            results.append(
                CheckResult(
                    f"Google: {name}",
                    "fail",
                    "Token refresh failed",
                    details=(
                        (refresh_error or "")
                        + " The refresh token is dead; re-authorize this "
                        "connector."
                    ).strip(),
                    section=SECTION_PROVIDERS,
                    live=True,
                    fix=f"reauth:{name}",
                )
            )
            continue

        try:
            response = httpx.get(
                _GOOGLE_PROBES[name],
                headers={"Authorization": f"Bearer {token}"},
                timeout=_PROVIDER_TIMEOUT,
            )
        except Exception as exc:
            results.append(
                CheckResult(
                    f"Google: {name}",
                    "warn",
                    f"Could not reach the API: {exc}",
                    section=SECTION_PROVIDERS,
                    live=True,
                )
            )
            continue

        if response.status_code == 200:
            results.append(
                CheckResult(
                    f"Google: {name}",
                    "ok",
                    "Token refreshed and the API answered",
                    section=SECTION_PROVIDERS,
                    live=True,
                )
            )
        elif response.status_code == 403:
            # The distinction that took months to diagnose: a 403 here is the
            # API being switched off in the project, not a missing scope.
            results.append(
                CheckResult(
                    f"Google: {name}",
                    "fail",
                    "Authorized, but the API refused (403)",
                    details=(
                        "Usually the API is disabled for the project rather "
                        "than a scope problem. Re-consenting will not fix it; "
                        "enabling the API in the Google console will."
                    ),
                    section=SECTION_PROVIDERS,
                    live=True,
                )
            )
        else:
            results.append(
                CheckResult(
                    f"Google: {name}",
                    "fail",
                    f"The API answered {response.status_code}",
                    section=SECTION_PROVIDERS,
                    live=True,
                )
            )
    return results


# Google Maps probes are the only checks here that spend a scarce, shared
# quota: Routes and Places are capped at 30 requests a day each, and a live
# run is reachable from chat, so a few casual "check yourself" requests could
# exhaust the allowance the car briefing depends on. One probe per API per day
# is about three percent of the cap; beyond that the last known result is
# reported with its age rather than a fresh call being made.
_MAPS_PROBES_PER_DAY = 1

# A route short enough to be trivial, and a query that matches everywhere.
_ROUTE_PROBE_ORIGIN = {"latitude": 14.2100, "longitude": 121.1650}
_ROUTE_PROBE_DESTINATION = {"latitude": 14.2110, "longitude": 121.1660}
_PLACES_PROBE_QUERY = "coffee"


def _probe_state_path() -> Path:
    return _config_dir() / "health_probes.json"


def _load_probe_state() -> Dict[str, Any]:
    try:
        return json.loads(_probe_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_probe_state(state: Dict[str, Any]) -> None:
    try:
        _probe_state_path().write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )
    except Exception:
        # A health check must never fail because it could not write a note to
        # itself.
        pass


def _probe_allowance(name: str) -> tuple[bool, Dict[str, Any]]:
    """Whether *name* may be probed today, and what is remembered about it."""
    state = _load_probe_state()
    entry = state.get(name) or {}
    today = time.strftime("%Y-%m-%d", time.gmtime())
    used = int(entry.get("count") or 0) if entry.get("day") == today else 0
    return used < _MAPS_PROBES_PER_DAY, entry


def _record_probe(name: str, status: str, message: str) -> None:
    state = _load_probe_state()
    today = time.strftime("%Y-%m-%d", time.gmtime())
    entry = state.get(name) or {}
    used = int(entry.get("count") or 0) if entry.get("day") == today else 0
    state[name] = {
        "day": today,
        "count": used + 1,
        "status": status,
        "message": message,
        "at": time.time(),
    }
    _save_probe_state(state)


def _remembered_result(name: str, entry: Dict[str, Any]) -> CheckResult:
    """Report what the last probe found, with its age, instead of spending."""
    status = str(entry.get("status") or "warn")
    message = str(entry.get("message") or "no result recorded")
    try:
        hours = max(0, int((time.time() - float(entry.get("at") or 0)) / 3600))
        age = f"{hours}h ago"
    except Exception:
        age = "earlier"
    return CheckResult(
        f"Google {name.title()}",
        status,
        f"{message} (checked {age})",
        details=(
            f"Not re-checked: {_MAPS_PROBES_PER_DAY} probe a day, because this "
            "API is capped at 30 requests daily and the car briefing needs "
            "them more than this page does."
        ),
        section=SECTION_PROVIDERS,
    )


def _check_google_maps(live: bool) -> List[CheckResult]:
    """Probe Routes and Places, at most once a day each.

    These are the APIs the drive briefing depends on, and the ones whose
    quota is small enough to matter. The probe uses the same functions the
    navigate tool calls, so a pass means the real path works rather than that
    some other endpoint answered.
    """
    config = _get_config()
    nav = getattr(config, "navigation", None)
    if nav is None:
        return []

    key = _provider_key("GOOGLE_MAPS_API_KEY")
    wanted = [
        (name, enabled)
        for name, enabled in (
            ("routes", bool(getattr(nav, "routes_enabled", False))),
            ("places", bool(getattr(nav, "places_enabled", False))),
        )
        if enabled
    ]
    if not wanted:
        return []

    if not key:
        return [
            CheckResult(
                "Google Maps",
                "fail",
                "Enabled but no API key",
                details="Navigation cannot resolve a destination or an ETA.",
                section=SECTION_PROVIDERS,
            )
        ]

    if not live:
        return [
            CheckResult(
                "Google Maps",
                "ok",
                f"{len(wanted)} APIs configured (not called)",
                details="A live check spends one request against each daily cap.",
                section=SECTION_PROVIDERS,
            )
        ]

    results: List[CheckResult] = []
    for name, _enabled in wanted:
        allowed, entry = _probe_allowance(name)
        if not allowed:
            results.append(_remembered_result(name, entry))
            continue

        try:
            from openjarvis.tools.navigate import compute_route, search_places
        except Exception as exc:
            results.append(
                CheckResult(
                    f"Google {name.title()}",
                    "warn",
                    f"Cannot probe: {exc}",
                    section=SECTION_PROVIDERS,
                )
            )
            continue

        try:
            if name == "routes":
                compute_route(
                    _ROUTE_PROBE_ORIGIN, _ROUTE_PROBE_DESTINATION, key
                )
                message = "A route was computed"
            else:
                search_places(_PLACES_PROBE_QUERY, key, None)
                message = "A place search returned"
            status = "ok"
        except Exception as exc:
            status = "fail"
            # Provider bodies can carry the key back; keep only the type.
            message = f"Request failed ({type(exc).__name__})"

        _record_probe(name, status, message)
        results.append(
            CheckResult(
                f"Google {name.title()}",
                status,
                message,
                details=(
                    None
                    if status == "ok"
                    else "The drive briefing depends on this; check the API is "
                    "enabled and within quota."
                ),
                section=SECTION_PROVIDERS,
                live=True,
            )
        )
    return results


def _check_deepgram(live: bool) -> List[CheckResult]:
    """Validate the Deepgram key with one real call.

    Flaky voice here has usually been DNS rather than code, so a check that
    only reads the key would keep pointing at the wrong thing.
    """
    try:
        from openjarvis.speech import flux
    except Exception:
        return []

    try:
        key = flux.api_key() or _provider_key("DEEPGRAM_API_KEY")
    except Exception:
        key = _provider_key("DEEPGRAM_API_KEY")

    if not key:
        return [
            CheckResult(
                "Deepgram",
                "warn",
                "No API key in the environment",
                details="Streaming speech-to-text will not connect.",
                section=SECTION_PROVIDERS,
            )
        ]

    if not live:
        return [
            CheckResult(
                "Deepgram",
                "ok",
                "Key present (not called)",
                section=SECTION_PROVIDERS,
            )
        ]

    try:
        import httpx

        response = httpx.get(
            "https://api.deepgram.com/v1/projects",
            headers={"Authorization": f"Token {key}"},
            timeout=_PROVIDER_TIMEOUT,
        )
    except Exception as exc:
        return [
            CheckResult(
                "Deepgram",
                "fail",
                f"Unreachable: {exc}",
                details="Check DNS before the code; that has been the cause before.",
                section=SECTION_PROVIDERS,
                live=True,
            )
        ]

    if response.status_code == 200:
        return [
            CheckResult(
                "Deepgram", "ok", "Key accepted", section=SECTION_PROVIDERS, live=True
            )
        ]
    return [
        CheckResult(
            "Deepgram",
            "fail",
            f"Key rejected ({response.status_code})",
            section=SECTION_PROVIDERS,
            live=True,
        )
    ]


def _check_cartesia(live: bool) -> List[CheckResult]:
    """Validate the speech synthesis key against the provider."""
    key = _provider_key("CARTESIA_API_KEY")
    if not key:
        return []

    if not live:
        return [
            CheckResult(
                "Cartesia",
                "ok",
                "Key present (not called)",
                section=SECTION_PROVIDERS,
            )
        ]

    try:
        import httpx

        # Listing voices is a real authenticated call without generating
        # audio, so it proves the key without spending synthesis credit.
        response = httpx.get(
            "https://api.cartesia.ai/voices/",
            headers={"X-API-Key": key, "Cartesia-Version": "2024-06-10"},
            timeout=_PROVIDER_TIMEOUT,
        )
    except Exception as exc:
        return [
            CheckResult(
                "Cartesia",
                "fail",
                f"Unreachable: {exc}",
                section=SECTION_PROVIDERS,
                live=True,
            )
        ]

    if response.status_code == 200:
        return [
            CheckResult(
                "Cartesia", "ok", "Key accepted", section=SECTION_PROVIDERS, live=True
            )
        ]
    return [
        CheckResult(
            "Cartesia",
            "fail",
            f"Key rejected ({response.status_code})",
            details="Sage's own voice will fall back to a local one.",
            section=SECTION_PROVIDERS,
            live=True,
        )
    ]


def _check_tavily(live: bool) -> List[CheckResult]:
    """Validate the search key. There is no fallback search engine."""
    key = _provider_key("TAVILY_API_KEY")
    if not key:
        return []

    if not live:
        return [
            CheckResult(
                "Tavily", "ok", "Key present (not called)", section=SECTION_PROVIDERS
            )
        ]

    try:
        import httpx

        response = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": key, "query": "ping", "max_results": 1},
            timeout=_PROVIDER_TIMEOUT,
        )
    except Exception as exc:
        return [
            CheckResult(
                "Tavily",
                "fail",
                f"Unreachable: {exc}",
                section=SECTION_PROVIDERS,
                live=True,
            )
        ]

    if response.status_code == 200:
        return [
            CheckResult(
                "Tavily", "ok", "Search returned", section=SECTION_PROVIDERS, live=True
            )
        ]
    return [
        CheckResult(
            "Tavily",
            "fail",
            f"Search failed ({response.status_code})",
            details="Web search has no fallback engine; it simply stops working.",
            section=SECTION_PROVIDERS,
            live=True,
        )
    ]


# -- Tools -------------------------------------------------------------------

# Tools whose allowlist is fail-closed: an unset variable means an empty list,
# which means the tool is enabled and can touch nothing. find_file is
# deliberately absent -- it falls back to sensible defaults when unset, so
# treating it the same way would report a working tool as broken.
_FAIL_CLOSED_TOOL_DIRS = {
    "coding_command": "OPENJARVIS_CODING_DIRS",
    "file_read": "OPENJARVIS_FILE_READ_DIRS",
    "file_write": "OPENJARVIS_FILE_WRITE_DIRS",
    "apply_patch": "OPENJARVIS_FILE_WRITE_DIRS",
    "git_status": "OPENJARVIS_GIT_DIRS",
    "git_diff": "OPENJARVIS_GIT_DIRS",
    "git_log": "OPENJARVIS_GIT_DIRS",
    "git_commit": "OPENJARVIS_GIT_DIRS",
}


def _configured_tools() -> List[str]:
    config = _get_config()
    raw = getattr(getattr(config, "agent", None), "tools", "") or ""
    if isinstance(raw, (list, tuple)):
        return [str(t).strip() for t in raw if str(t).strip()]
    return [t.strip() for t in str(raw).split(",") if t.strip()]


def _check_configured_tools_registered() -> List[CheckResult]:
    """Every tool named in config must exist in the registry.

    A name in config that never registered is a capability the user believes
    they have and does not. Nothing reports it today: the model simply never
    calls a tool it was never offered.
    """
    configured = _configured_tools()
    if not configured:
        return [
            CheckResult(
                "Configured tools",
                "warn",
                "No tools are enabled",
                section=SECTION_TOOLS,
            )
        ]

    try:
        import openjarvis.tools  # noqa: F401
        from openjarvis.core.registry import ToolRegistry

        registered = set(ToolRegistry.keys())
    except Exception as exc:
        return [
            CheckResult(
                "Configured tools",
                "warn",
                f"Could not read the tool registry: {exc}",
                section=SECTION_TOOLS,
            )
        ]

    missing = sorted(t for t in configured if t not in registered)
    if missing:
        return [
            CheckResult(
                "Configured tools",
                "fail",
                f"Enabled but not registered: {', '.join(missing)}",
                details=(
                    "These are named in [agent] tools and do not exist, so "
                    "Sage is never offered them and will never call them. "
                    "Usually a typo or a module that failed to import."
                ),
                section=SECTION_TOOLS,
            )
        ]
    return [
        CheckResult(
            "Configured tools",
            "ok",
            f"All {len(configured)} enabled tools are registered",
            section=SECTION_TOOLS,
        )
    ]


def _check_tool_modules_import() -> List[CheckResult]:
    """Import every tool module and report the ones that fail.

    ``openjarvis/tools/__init__.py`` wraps each import in ``except
    ImportError: pass`` -- fifty-one of them. That is deliberate, so a missing
    optional dependency does not take the whole package down, but it also
    means a broken tool disappears in total silence. This is the only place
    that difference becomes visible.
    """
    import importlib

    try:
        import openjarvis.tools as tools_pkg
    except Exception as exc:
        return [
            CheckResult(
                "Tool modules",
                "fail",
                f"The tools package will not import: {exc}",
                section=SECTION_TOOLS,
            )
        ]

    package_dir = Path(tools_pkg.__file__).parent
    broken: List[str] = []
    checked = 0
    for module_path in sorted(package_dir.glob("*.py")):
        name = module_path.stem
        if name.startswith("_"):
            continue
        checked += 1
        try:
            importlib.import_module(f"openjarvis.tools.{name}")
        except Exception as exc:
            broken.append(f"{name} ({type(exc).__name__})")

    if broken:
        return [
            CheckResult(
                "Tool modules",
                "fail",
                f"Will not import: {', '.join(broken)}",
                details=(
                    "The package swallows these failures, so the tools they "
                    "define are silently absent from the registry."
                ),
                section=SECTION_TOOLS,
            )
        ]
    return [
        CheckResult(
            "Tool modules",
            "ok",
            f"All {checked} tool modules import",
            section=SECTION_TOOLS,
        )
    ]


def _check_tool_directories() -> List[CheckResult]:
    """Fail-closed tools must have a directory allowlist that exists.

    These read their allowlist from an environment variable and fall back to
    an empty list, not to a default. Enabled with the variable unset, the tool
    is offered to the model, accepts the call and can reach nothing.
    """
    configured = set(_configured_tools())
    wanted = {
        tool: var for tool, var in _FAIL_CLOSED_TOOL_DIRS.items() if tool in configured
    }
    if not wanted:
        return []

    unset: Dict[str, List[str]] = {}
    missing_paths: Dict[str, List[str]] = {}
    for tool, var in sorted(wanted.items()):
        raw = os.environ.get(var, "")
        entries = [part.strip() for part in raw.split(os.pathsep) if part.strip()]
        if not entries:
            unset.setdefault(var, []).append(tool)
            continue
        absent = [entry for entry in entries if not Path(entry).is_dir()]
        if absent:
            missing_paths.setdefault(var, []).extend(absent)

    results: List[CheckResult] = []
    if unset:
        detail = "; ".join(
            f"{var} (needed by {', '.join(sorted(tools))})"
            for var, tools in sorted(unset.items())
        )
        results.append(
            CheckResult(
                "Tool directories",
                "fail",
                "Enabled tools have no directory allowlist",
                details=(
                    f"{detail}. These tools are fail-closed: with the variable "
                    "unset they accept a call and can reach nothing. Note that "
                    "setx needs a new shell, and Sage needs a restart."
                ),
                section=SECTION_TOOLS,
            )
        )
    if missing_paths:
        detail = "; ".join(
            f"{var}: {', '.join(sorted(set(paths)))}"
            for var, paths in sorted(missing_paths.items())
        )
        results.append(
            CheckResult(
                "Tool directories",
                "warn",
                "Allowlisted directories do not exist",
                details=detail,
                section=SECTION_TOOLS,
            )
        )
    if not results:
        results.append(
            CheckResult(
                "Tool directories",
                "ok",
                f"{len(wanted)} fail-closed tools have usable allowlists",
                section=SECTION_TOOLS,
            )
        )
    return results


# -- Entry point -------------------------------------------------------------


def run_health_checks(
    *, live: bool = False, app_state: Any = None
) -> HealthReport:
    """Run every diagnostic check and return them grouped by section.

    ``live`` permits outbound calls that may be billable or quota-limited.
    The default run reads local state only.

    This is the single entry point. Adding a check here reaches the CLI, the
    Health page and the ``system_health`` tool at once; assembling a list
    anywhere else creates the second path that this codebase keeps losing
    track of.
    """
    checks: List[CheckResult] = []

    checks.append(_check_python_version())
    checks.append(_check_config_exists())
    checks.append(_check_config_parses())
    checks.append(_check_security_profile())
    checks.append(_check_nodejs())
    checks.extend(_check_optional_deps())

    checks.append(_check_speech_backend())
    checks.append(_check_wake_word())
    checks.append(_check_tts_credentials())
    checks.append(_check_flux_streaming())
    checks.append(_check_speech_device())

    checks.extend(_check_engines(live))
    checks.extend(_check_models(live))
    checks.append(_check_default_model(live))
    checks.append(_check_gpu())

    checks.extend(_check_scheduled_jobs())
    checks.extend(_check_credentials())
    checks.extend(_check_connectors())

    checks.extend(_check_digest_sections())
    checks.append(_check_scheduler_running())
    checks.extend(_check_telemetry_recording(app_state))
    checks.extend(_check_presence(app_state))

    checks.extend(_check_configured_tools_registered())
    checks.extend(_check_tool_modules_import())
    checks.extend(_check_tool_directories())

    checks.extend(_check_google_apis(live))
    checks.extend(_check_google_maps(live))
    checks.extend(_check_deepgram(live))
    checks.extend(_check_cartesia(live))
    checks.extend(_check_tavily(live))

    return HealthReport(checks=checks, live=live)


def results_to_dicts(checks: List[CheckResult]) -> List[Dict[str, Any]]:
    """Convert CheckResult list to JSON-serializable dicts."""
    return [asdict(c) for c in checks]


__all__ = [
    "CheckResult",
    "HealthReport",
    "SECTION_LABELS",
    "SECTION_ORDER",
    "results_to_dicts",
    "run_health_checks",
]
