"""Tests for the feature-wiring checks.

Each of these targets a failure that ran unreported for days: a briefing
section that collected nothing, a dead scheduler behind healthy-looking jobs,
and a dashboard reading zero because engines went unwrapped. Config was
correct in all three cases, which is why v1's config-presence checks could not
see them.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from openjarvis.core.health import (
    SECTION_FEATURES,
    _check_digest_sections,
    _check_scheduler_running,
    _check_telemetry_recording,
)


class _Section:
    def __init__(self, sources=None) -> None:
        self.sources = sources or []


def _digest(**kwargs):
    base = SimpleNamespace(enabled=True, sections=[])
    for k, v in kwargs.items():
        setattr(base, k, v)
    return SimpleNamespace(digest=base)


class TestDigestSections:
    def test_a_section_resolving_to_nothing_fails(self) -> None:
        # "world" with an explicitly empty list is the shape of the original
        # bug; a section with no default and no sources is the same fault.
        config = _digest(sections=["invented"], invented=_Section([]))
        with patch("openjarvis.core.health._get_config", return_value=config):
            results = _check_digest_sections()
        assert results[0].status == "fail"
        assert "invented" in results[0].message
        assert results[0].section == SECTION_FEATURES

    def test_a_section_using_its_defaults_is_fine(self) -> None:
        config = _digest(sections=["world"], world=_Section([]))
        with patch("openjarvis.core.health._get_config", return_value=config):
            results = _check_digest_sections()
        assert results[0].status == "ok"

    def test_configured_sources_are_accepted(self) -> None:
        config = _digest(sections=["messages"], messages=_Section(["gmail"]))
        with patch("openjarvis.core.health._get_config", return_value=config):
            results = _check_digest_sections()
        assert results[0].status == "ok"

    def test_browser_backed_sections_are_not_called_silent(self) -> None:
        # Teams has no connector and is scraped through the browser. The first
        # run of this check reported it as broken, which is the crying-wolf
        # failure a health page must not have.
        config = _digest(sections=["teams"], teams=_Section([]))
        with patch("openjarvis.core.health._get_config", return_value=config):
            results = _check_digest_sections()
        assert results[0].status == "ok"

    def test_a_disabled_briefing_is_not_a_fault(self) -> None:
        config = SimpleNamespace(digest=SimpleNamespace(enabled=False))
        with patch("openjarvis.core.health._get_config", return_value=config):
            results = _check_digest_sections()
        assert results[0].status == "ok"


class _DeadThread:
    def is_alive(self) -> bool:
        return False


class _LiveThread:
    def is_alive(self) -> bool:
        return True


class TestSchedulerRunning:
    def _run(self, scheduler):
        with patch(
            "openjarvis.scheduler.tools.ListScheduledTasksTool._scheduler",
            scheduler,
            create=True,
        ):
            return _check_scheduler_running()

    def test_a_dead_poll_thread_is_a_failure(self) -> None:
        result = self._run(SimpleNamespace(_thread=_DeadThread()))
        assert result.status == "fail"

    def test_a_live_poll_thread_is_ok(self) -> None:
        result = self._run(SimpleNamespace(_thread=_LiveThread()))
        assert result.status == "ok"

    def test_no_scheduler_is_a_warning_not_a_failure(self) -> None:
        # The CLI legitimately has no scheduler; only the server's own report
        # saying this means something is wrong.
        result = self._run(None)
        assert result.status == "warn"


class _Instrumented:
    pass


class TestTelemetryInstrumentation:
    def _run(self, engine):
        state = SimpleNamespace(engine=engine)
        with patch(
            "openjarvis.telemetry.instrumented_engine.InstrumentedEngine",
            _Instrumented,
        ):
            return _check_telemetry_recording(state)

    def test_without_app_state_the_check_is_skipped(self) -> None:
        assert _check_telemetry_recording(None) == []

    def test_an_unwrapped_engine_in_the_group_fails(self) -> None:
        engine = SimpleNamespace(
            _engines=[("ollama", _Instrumented()), ("cloud", object())]
        )
        results = self._run(engine)
        assert results[0].status == "fail"
        assert "cloud" in results[0].message

    def test_all_wrapped_is_ok(self) -> None:
        engine = SimpleNamespace(
            _engines=[("ollama", _Instrumented()), ("cloud", _Instrumented())]
        )
        assert self._run(engine)[0].status == "ok"

    def test_a_single_unwrapped_engine_fails(self) -> None:
        assert self._run(object())[0].status == "fail"

    def test_no_engine_is_reported(self) -> None:
        results = _check_telemetry_recording(SimpleNamespace(engine=None))
        assert results[0].status == "warn"
