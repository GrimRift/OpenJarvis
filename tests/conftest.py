"""Shared fixtures — clear all registries and the event bus between tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openjarvis.core.config import GpuInfo, HardwareInfo
from openjarvis.core.events import EventBus, reset_event_bus
from openjarvis.core.registry import (
    AgentRegistry,
    BenchmarkRegistry,
    ChannelRegistry,
    CompressionRegistry,
    ConnectorRegistry,
    EngineRegistry,
    FactStoreRegistry,
    MemoryRegistry,
    MinerRegistry,
    ModelRegistry,
    RouterPolicyRegistry,
    SkillRegistry,
    SpeechRegistry,
    ToolRegistry,
    TTSRegistry,
)


@pytest.fixture(autouse=True)
def _no_update_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let the CLI's PyPI update-check nag run during tests.

    ``check_for_updates`` writes its banner to stderr, which ``CliRunner``
    merges into ``result.output`` — polluting JSON/CSV output of any test
    that invokes a CLI command. It already self-disables when ``CI`` is
    set, but that only helps in CI; locally (e.g. a dev with a stale
    version-check cache and network access) it fires for real.
    """
    monkeypatch.setenv("OPENJARVIS_NO_UPDATE_CHECK", "1")


@pytest.fixture(autouse=True)
def _no_search_read_ahead(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test search must not start real page reads in the background."""
    try:
        import openjarvis.tools.web_read as web_read
    except Exception:  # noqa: BLE001
        return
    monkeypatch.setattr(web_read, "PREFETCH_TOP", 0)


@pytest.fixture(autouse=True)
def _no_real_analytics_senders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test-created SDK clients must not send events or leave exit hooks waiting.

    Each real PostHog client otherwise starts a consumer whose 30-second
    queue wait is joined serially at exit, adding minutes after the summary.
    """
    from posthog import Posthog

    real_init = Posthog.__init__

    def _init(self, *args, **kwargs):
        kwargs["send"] = False
        kwargs["disabled"] = True
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(Posthog, "__init__", _init)


@pytest.fixture(autouse=True)
def _no_real_voice_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Settings voice choice lives in the data directory; a test that
    resolves a TTS provider must see a fresh default, not this machine's
    (the route tests failed here the moment the real choice was
    chatterbox)."""
    from openjarvis.speech import voice_choice

    monkeypatch.setattr(
        voice_choice,
        "load_choice",
        lambda config_dir=None: (
            voice_choice.VoiceChoice()
            if config_dir is None
            else voice_choice._load_choice_from(config_dir)
        ),
    )


@pytest.fixture(autouse=True)
def _no_real_brief_memory(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Briefs read earlier briefs from digest.db/traces.db and ask GitHub for
    CI status; tests get an empty temp folder and no network call."""
    try:
        from openjarvis.agents import brief_memory
    except Exception:
        return
    root = tmp_path_factory.mktemp("briefs")
    monkeypatch.setattr(brief_memory, "get_config_dir", lambda: root)
    monkeypatch.setattr(brief_memory, "ci_status", lambda timeout=10.0: "")


@pytest.fixture(autouse=True)
def _fresh_memory_search_budget():
    """Each test is its own message: the per-message limit on memory
    searches would otherwise carry over from one test to the next."""
    try:
        from openjarvis.tools import memory_budget
    except Exception:
        yield
        return
    memory_budget.start_message()
    yield
    memory_budget.start_message()


@pytest.fixture(autouse=True)
def _no_real_memory_settings(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every turn's context reads the Memory page settings; tests get the
    defaults from a temp folder, not whatever the live data folder holds."""
    try:
        from openjarvis.memory import settings as memory_settings
    except Exception:
        return
    monkeypatch.setattr(
        memory_settings, "DEFAULT_CONFIG_DIR", tmp_path_factory.mktemp("memset")
    )


@pytest.fixture(autouse=True)
def _no_real_image_state(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M40 keeps an index and settings in the data directory and writes PNGs
    to the user's Pictures folder. A test that forgets to pass its own paths
    gets temp ones, never the live index or a real picture."""
    try:
        from openjarvis.images import settings as image_settings
        from openjarvis.images import store as image_store
    except Exception:
        return
    root = tmp_path_factory.mktemp("images")
    monkeypatch.setattr(image_store, "get_config_dir", lambda: root)
    monkeypatch.setattr(image_settings, "get_config_dir", lambda: root)
    monkeypatch.setattr(
        image_settings, "default_save_dir", lambda: str(root / "Pictures")
    )


@pytest.fixture(autouse=True)
def _no_real_speaker_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """The voice fingerprint learns from every confirmed wake word and asks
    the real sidecar: left on, the route tests' fake audio was taught to
    this machine's profile of the user's voice."""
    try:
        from openjarvis.speech import speaker_id
    except Exception:
        return
    monkeypatch.setattr(speaker_id, "get", lambda config: None)


@pytest.fixture(autouse=True)
def _no_real_telemetry_db(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A default ``JarvisConfig()`` puts telemetry in the data directory, and
    on this machine ``OPENJARVIS_HOME`` is the live ``OpenJarvis-Data``: the
    SDK tests' mock engine wrote hundreds of ``test-model``/``custom-model``
    rows into the real telemetry.db. Any store opened on that default path
    gets a temp file instead; tests that pass their own path are untouched."""
    from openjarvis.core import config as _config
    from openjarvis.core.paths import get_config_dir
    from openjarvis.telemetry.store import TelemetryStore

    real_paths = {
        (get_config_dir() / "telemetry.db").resolve(),
        (_config.DEFAULT_CONFIG_DIR / "telemetry.db").resolve(),
    }
    real_init = TelemetryStore.__init__

    def _init(self, db_path, *args, **kwargs):
        if (
            str(db_path) != ":memory:"
            and Path(db_path).expanduser().resolve() in real_paths
        ):
            db_path = tmp_path_factory.mktemp("telemetry") / "telemetry.db"
        real_init(self, db_path, *args, **kwargs)

    monkeypatch.setattr(TelemetryStore, "__init__", _init)


@pytest.fixture(autouse=True)
def _nobody_speaking() -> None:
    """Any test that plays sound leaves the server "speaking" for the echo
    tail (2 s), and the wake-word socket and Flux relay go deaf for it --
    which made the route tests fail only after the volume tests."""
    try:
        from openjarvis.speech import player
    except Exception:  # noqa: BLE001 -- speech extras may be absent
        yield
        return
    player._speakers = 0
    player._last_spoke_at = 0.0
    yield
    player._speakers = 0
    player._last_spoke_at = 0.0


@pytest.fixture(autouse=True)
def _no_activity_left_over() -> None:
    """The activity record (a reply being read aloud, the user's last turn)
    is process-wide, and the presence monitor reads it: a test that left a
    reply "just ended" made the next test's empty desk read as present --
    seven moments tests failed whenever the ducking tests ran just before
    them."""
    from openjarvis.core import activity

    activity.reset()
    yield
    activity.reset()


@pytest.fixture(autouse=True)
def _clean_registries() -> None:
    """Ensure each test starts with empty registries and a fresh event bus."""
    ModelRegistry.clear()
    EngineRegistry.clear()
    MemoryRegistry.clear()
    FactStoreRegistry.clear()
    MinerRegistry.clear()
    AgentRegistry.clear()
    ToolRegistry.clear()
    RouterPolicyRegistry.clear()
    BenchmarkRegistry.clear()
    ChannelRegistry.clear()
    SpeechRegistry.clear()
    CompressionRegistry.clear()
    ConnectorRegistry.clear()
    TTSRegistry.clear()
    SkillRegistry.clear()
    reset_event_bus()


# ---------------------------------------------------------------------------
# Hardware fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def nvidia_gpu() -> GpuInfo:
    """NVIDIA A100 GPU fixture."""
    return GpuInfo(vendor="nvidia", name="NVIDIA A100-SXM4-80GB", vram_gb=80.0, count=1)


@pytest.fixture
def nvidia_consumer_gpu() -> GpuInfo:
    """NVIDIA consumer GPU fixture."""
    return GpuInfo(
        vendor="nvidia",
        name="NVIDIA GeForce RTX 4090",
        vram_gb=24.0,
        count=1,
    )


@pytest.fixture
def nvidia_multi_gpu() -> GpuInfo:
    """NVIDIA multi-GPU fixture."""
    return GpuInfo(vendor="nvidia", name="NVIDIA H100", vram_gb=80.0, count=4)


@pytest.fixture
def amd_gpu() -> GpuInfo:
    """AMD MI300X GPU fixture."""
    return GpuInfo(vendor="amd", name="AMD Instinct MI300X", vram_gb=192.0, count=1)


@pytest.fixture
def apple_gpu() -> GpuInfo:
    """Apple Silicon GPU fixture."""
    return GpuInfo(vendor="apple", name="Apple M4 Max", vram_gb=128.0, count=1)


@pytest.fixture
def hardware_nvidia(nvidia_gpu: GpuInfo) -> HardwareInfo:
    """Full NVIDIA hardware profile."""
    return HardwareInfo(
        platform="linux",
        cpu_brand="AMD EPYC 7763",
        cpu_count=64,
        ram_gb=512.0,
        gpu=nvidia_gpu,
    )


@pytest.fixture
def hardware_nvidia_consumer(nvidia_consumer_gpu: GpuInfo) -> HardwareInfo:
    """Consumer NVIDIA hardware profile."""
    return HardwareInfo(
        platform="linux",
        cpu_brand="Intel Core i9-14900K",
        cpu_count=24,
        ram_gb=64.0,
        gpu=nvidia_consumer_gpu,
    )


@pytest.fixture
def hardware_amd(amd_gpu: GpuInfo) -> HardwareInfo:
    """Full AMD hardware profile."""
    return HardwareInfo(
        platform="linux",
        cpu_brand="AMD EPYC 9654",
        cpu_count=96,
        ram_gb=768.0,
        gpu=amd_gpu,
    )


@pytest.fixture
def hardware_apple(apple_gpu: GpuInfo) -> HardwareInfo:
    """Apple Silicon hardware profile."""
    return HardwareInfo(
        platform="darwin",
        cpu_brand="Apple M4 Max",
        cpu_count=16,
        ram_gb=128.0,
        gpu=apple_gpu,
    )


@pytest.fixture
def hardware_cpu_only() -> HardwareInfo:
    """CPU-only hardware profile (no GPU)."""
    return HardwareInfo(
        platform="linux",
        cpu_brand="Intel Xeon E5-2686 v4",
        cpu_count=8,
        ram_gb=32.0,
        gpu=None,
    )


# ---------------------------------------------------------------------------
# Engine availability fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def has_ollama() -> bool:
    """Check if Ollama is running locally."""
    try:
        import httpx

        resp = httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


@pytest.fixture
def has_vllm() -> bool:
    """Check if vLLM is running locally."""
    try:
        import httpx

        resp = httpx.get("http://localhost:8000/v1/models", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


@pytest.fixture
def has_llamacpp() -> bool:
    """Check if llama.cpp server is running locally."""
    try:
        import httpx

        resp = httpx.get("http://localhost:8080/v1/models", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Cloud API key fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def has_openai_key() -> bool:
    """Check if OPENAI_API_KEY is set."""
    return bool(os.environ.get("OPENAI_API_KEY"))


@pytest.fixture
def has_anthropic_key() -> bool:
    """Check if ANTHROPIC_API_KEY is set."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


@pytest.fixture
def has_gemini_key() -> bool:
    """Check if GEMINI_API_KEY or GOOGLE_API_KEY is set."""
    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


# ---------------------------------------------------------------------------
# Mock engine factory
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_engine():
    """Factory for mock InferenceEngine instances."""

    def _factory(
        engine_id: str = "mock",
        model_response: str = "Hello!",
        tool_calls: list | None = None,
        models: list[str] | None = None,
    ) -> MagicMock:
        engine = MagicMock()
        engine.engine_id = engine_id
        engine.health.return_value = True
        engine.list_models.return_value = models or ["test-model"]

        result = {
            "content": model_response,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "test-model",
            "finish_reason": "stop",
        }
        if tool_calls:
            result["tool_calls"] = tool_calls
            result["finish_reason"] = "tool_calls"
        engine.generate.return_value = result
        return engine

    return _factory


@pytest.fixture
def event_bus() -> EventBus:
    """Fresh EventBus with history recording enabled."""
    return EventBus(record_history=True)


# ---------------------------------------------------------------------------
# Mining sidecar fixtures (shared across tests/mining/ and tests/engine/)
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_sidecar_payload() -> dict:
    """A valid vllm-pearl sidecar payload with all expected fields."""
    return {
        "provider": "vllm-pearl",
        "vllm_endpoint": "http://127.0.0.1:8000/v1",
        "model": "pearl-ai/Llama-3.3-70B-Instruct-pearl",
        "gateway_url": "http://127.0.0.1:8337",
        "gateway_metrics_url": "http://127.0.0.1:8339",
        "container_id": "abc123def456",
        "wallet_address": "prl1qexampleaddress",
        "started_at": 1714867200,
    }


@pytest.fixture
def sidecar_path(tmp_path: Path) -> Path:
    """Path to a (not-yet-written) mining sidecar JSON file."""
    return tmp_path / "mining.json"


@pytest.fixture
def written_sidecar(sidecar_path: Path, sample_sidecar_payload: dict) -> Path:
    """A written mining sidecar JSON file; returns the path."""
    sidecar_path.write_text(json.dumps(sample_sidecar_payload))
    return sidecar_path
