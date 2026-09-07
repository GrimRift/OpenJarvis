"""Tests for the shared diagnostic checks.

The credential shape matrix and the engine gating are here because both
shipped wrong the first time: every connector was judged by the OAuth rule,
which reported the weather API key and the Obsidian vault path as broken,
and every registered engine was probed, which produced ten "Unreachable"
warnings for engines that were never configured. Both faults made the report
less trustworthy than saying nothing, which is the one thing a health check
must not do.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

from openjarvis.core.health import (
    SECTION_CREDENTIALS,
    CheckResult,
    HealthReport,
    _check_connectors,
    _check_credentials,
    _check_engines,
    _check_optional_deps,
    _configured_engine_keys,
    _token_file_report,
    run_health_checks,
)


class TestCheckResult:
    def test_defaults_to_the_system_section_and_local(self) -> None:
        result = CheckResult("A check", "ok", "fine")
        assert result.section == "system"
        assert result.live is False
        assert result.fix is None


class TestHealthReport:
    def test_status_is_the_worst_check(self) -> None:
        assert HealthReport(checks=[CheckResult("a", "ok", "")]).status == "ok"
        assert (
            HealthReport(
                checks=[CheckResult("a", "ok", ""), CheckResult("b", "warn", "")]
            ).status
            == "warn"
        )
        assert (
            HealthReport(
                checks=[CheckResult("b", "warn", ""), CheckResult("c", "fail", "")]
            ).status
            == "fail"
        )

    def test_empty_sections_are_omitted(self) -> None:
        report = HealthReport(checks=[CheckResult("a", "ok", "", section="voice")])
        ids = [s["id"] for s in report.to_dict()["sections"]]
        assert ids == ["voice"]

    def test_summarize_names_only_the_problems(self) -> None:
        report = HealthReport(
            checks=[
                CheckResult("Good", "ok", "fine"),
                CheckResult("Bad", "fail", "broken"),
            ]
        )
        summary = report.summarize()
        assert "Bad: broken" in summary
        assert "Good" not in summary

    def test_summarize_says_so_when_everything_passes(self) -> None:
        report = HealthReport(checks=[CheckResult("Good", "ok", "fine")])
        assert "passed" in report.summarize()


class TestCredentialShapes:
    """Each connector shape gets the rule that fits it, not the OAuth rule."""

    def _write(self, tmp_path: Path, name: str, payload: dict) -> Path:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_oauth_with_refresh_token_is_ok(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path, "gmail", {"access_token": "x", "refresh_token": "y"}
        )
        result = _token_file_report(path)
        assert result.status == "ok"
        assert result.section == SECTION_CREDENTIALS

    def test_oauth_without_refresh_token_fails(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "gmail", {"access_token": "x"})
        result = _token_file_report(path)
        assert result.status == "fail"
        assert result.fix == "reauth:gmail"

    def test_an_api_key_is_not_judged_by_the_oauth_rule(self, tmp_path: Path) -> None:
        # The weather connector stores an API key and no refresh token; it was
        # reported as broken until the shape decided the rule.
        path = self._write(tmp_path, "weather", {"api_key": "k", "units": "metric"})
        assert _token_file_report(path).status == "ok"

    def test_a_local_path_connector_needs_no_credential(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "obsidian", {"path": "C:/vault"})
        assert _token_file_report(path).status == "ok"

    def test_registered_but_unauthorized_is_a_warning(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path, "outlook", {"client_id": "a", "client_secret": "b"}
        )
        result = _token_file_report(path)
        assert result.status == "warn"
        assert result.fix == "reauth:outlook"

    def test_a_stale_refresh_token_is_flagged(self, tmp_path: Path) -> None:
        path = self._write(
            tmp_path, "gcalendar", {"access_token": "x", "refresh_token": "y"}
        )
        old = time.time() - (30 * 86400)
        os.utime(path, (old, old))
        result = _token_file_report(path)
        assert result.status == "warn"
        assert "30 days" in result.message

    def test_unreadable_file_fails_rather_than_raising(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        assert _token_file_report(path).status == "fail"


class TestConnectorActivation:
    def test_activated_without_a_token_is_a_failure(self, tmp_path: Path) -> None:
        connectors = tmp_path / "connectors"
        connectors.mkdir()
        (connectors / "_activated.json").write_text(
            json.dumps({"sources": ["gmail"]}), encoding="utf-8"
        )
        with patch("openjarvis.core.health._config_dir", return_value=tmp_path):
            results = _check_connectors()
        assert [r.status for r in results] == ["fail"]
        assert results[0].fix == "reauth:gmail"

    def test_activated_with_a_token_is_ok(self, tmp_path: Path) -> None:
        connectors = tmp_path / "connectors"
        connectors.mkdir()
        (connectors / "_activated.json").write_text(
            json.dumps({"sources": ["gmail"]}), encoding="utf-8"
        )
        (connectors / "gmail.json").write_text(json.dumps({}), encoding="utf-8")
        with patch("openjarvis.core.health._config_dir", return_value=tmp_path):
            results = _check_connectors()
        assert [r.status for r in results] == ["ok"]

    def test_missing_connector_directory_is_reported_not_raised(
        self, tmp_path: Path
    ) -> None:
        with patch("openjarvis.core.health._config_dir", return_value=tmp_path):
            results = _check_credentials()
        assert results[0].status == "warn"


class _FakeEngine:
    def __init__(self, is_cloud: bool) -> None:
        self.is_cloud = is_cloud
        self.probed = False

    def health(self) -> bool:
        self.probed = True
        return True

    def list_models(self) -> list:
        return ["a-model"]


class TestEngineGating:
    """A cloud probe costs money, and an unconfigured engine is not a fault."""

    def _run(self, live: bool, engines: dict, configured: set) -> Any:
        with (
            patch("openjarvis.core.health._ensure_engines_imported"),
            patch("openjarvis.core.health._get_config", return_value=object()),
            patch(
                "openjarvis.core.health._configured_engine_keys",
                return_value=configured,
            ),
            patch("openjarvis.core.registry.EngineRegistry.keys", return_value=engines),
            patch(
                "openjarvis.engine._discovery._make_engine",
                side_effect=lambda key, _cfg: engines[key],
            ),
        ):
            return _check_engines(live=live)

    def test_an_unconfigured_engine_is_not_a_warning(self) -> None:
        engines = {"vllm": _FakeEngine(is_cloud=False)}
        results = self._run(live=False, engines=engines, configured=set())
        assert [r.status for r in results] == ["ok"]
        assert results[0].message == "Not configured"
        assert engines["vllm"].probed is False

    def test_a_cloud_engine_is_not_probed_on_a_local_run(self) -> None:
        engines = {"cloud": _FakeEngine(is_cloud=True)}
        results = self._run(live=False, engines=engines, configured={"cloud"})
        assert engines["cloud"].probed is False
        assert results[0].status == "ok"
        assert "not probed" in results[0].message

    def test_a_cloud_engine_is_probed_on_a_live_run(self) -> None:
        engines = {"cloud": _FakeEngine(is_cloud=True)}
        results = self._run(live=True, engines=engines, configured={"cloud"})
        assert engines["cloud"].probed is True
        assert results[0].live is True

    def test_a_local_engine_is_probed_without_going_live(self) -> None:
        engines = {"ollama": _FakeEngine(is_cloud=False)}
        results = self._run(live=False, engines=engines, configured={"ollama"})
        assert engines["ollama"].probed is True
        assert results[0].status == "ok"


class TestConfiguredEngineKeys:
    def test_collects_the_default_and_every_section_override(self) -> None:
        class _Section:
            engine = "cloud"

        class _EngineSection:
            default = "ollama"

        class _Config:
            engine = _EngineSection()
            digest = _Section()

        assert _configured_engine_keys(_Config()) == {"ollama", "cloud"}


class TestOptionalDeps:
    def test_inapplicable_packages_are_not_warnings(self) -> None:
        with patch("openjarvis.core.health.shutil.which", return_value="/usr/bin/x"):
            results = _check_optional_deps()
        apple = next(r for r in results if "Apple Silicon" in r.name)
        assert apple.status == "ok"
        assert "Not applicable" in apple.message


class TestRunHealthChecks:
    def test_local_run_is_marked_local(self) -> None:
        report = run_health_checks(live=False)
        assert report.live is False
        assert report.checks
        assert not any(c.live for c in report.checks)

    def test_every_check_declares_a_known_section(self) -> None:
        from openjarvis.core.health import SECTION_ORDER

        report = run_health_checks(live=False)
        assert all(c.section in SECTION_ORDER for c in report.checks)
