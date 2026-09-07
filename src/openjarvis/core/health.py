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

SECTION_ORDER = (
    SECTION_SYSTEM,
    SECTION_VOICE,
    SECTION_MODELS,
    SECTION_JOBS,
    SECTION_CREDENTIALS,
    SECTION_CONNECTORS,
)

SECTION_LABELS = {
    SECTION_SYSTEM: "System",
    SECTION_VOICE: "Voice pipeline",
    SECTION_MODELS: "Models and GPU",
    SECTION_JOBS: "Scheduled jobs",
    SECTION_CREDENTIALS: "Credentials",
    SECTION_CONNECTORS: "Connectors",
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
        os.environ.get("CARTESIA_API_KEY") or os.environ.get("OPENJARVIS_TTS_API_KEY")
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


# -- Entry point -------------------------------------------------------------


def run_health_checks(*, live: bool = False) -> HealthReport:
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

    checks.extend(_check_engines(live))
    checks.extend(_check_models(live))
    checks.append(_check_default_model(live))
    checks.append(_check_gpu())

    checks.extend(_check_scheduled_jobs())
    checks.extend(_check_credentials())
    checks.extend(_check_connectors())

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
