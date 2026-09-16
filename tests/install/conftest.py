"""Test fixtures for installer / cold-start refresh tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_openjarvis_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``DEFAULT_CONFIG_DIR`` at a tmpdir for isolated tests.

    Returns the directory; teardown is automatic via tmp_path.
    """
    home = tmp_path / ".openjarvis"
    home.mkdir()
    (home / ".state").mkdir()
    (home / ".state" / "models").mkdir()
    (home / ".scripts").mkdir()
    config_path = home / "config.toml"
    monkeypatch.setattr("openjarvis.core.config.DEFAULT_CONFIG_DIR", home)
    monkeypatch.setattr("openjarvis.core.config.DEFAULT_CONFIG_PATH", config_path)
    # Also patch init_cmd's module-level bindings (imported with ``from ... import``).
    monkeypatch.setattr("openjarvis.cli.init_cmd.DEFAULT_CONFIG_DIR", home)
    monkeypatch.setattr("openjarvis.cli.init_cmd.DEFAULT_CONFIG_PATH", config_path)
    # doctor_cmd stopped binding the path at module level with M34 (it reads
    # the config through the health checks now); patch it only if it exists.
    monkeypatch.setattr(
        "openjarvis.cli.doctor_cmd.DEFAULT_CONFIG_PATH", config_path, raising=False
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    return home
