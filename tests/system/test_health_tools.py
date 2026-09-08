"""Tests for the tool checks.

42 tools are enabled and, before these, none were verified. The failure being
guarded is silent absence: ``openjarvis/tools/__init__.py`` wraps every import
in ``except ImportError: pass`` -- fifty-one times -- so a broken tool simply
does not appear, and a model never calls a tool it was not offered.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from openjarvis.core.health import (
    SECTION_TOOLS,
    _check_configured_tools_registered,
    _check_tool_directories,
    _check_tool_modules_import,
)


def _config(tools: str):
    return SimpleNamespace(agent=SimpleNamespace(tools=tools))


class TestConfiguredToolsRegistered:
    def test_a_configured_tool_that_never_registered_fails(self) -> None:
        with (
            patch(
                "openjarvis.core.health._get_config",
                return_value=_config("calculator,not_a_real_tool"),
            ),
            patch(
                "openjarvis.core.registry.ToolRegistry.keys",
                return_value=["calculator"],
            ),
        ):
            results = _check_configured_tools_registered()
        assert results[0].status == "fail"
        assert "not_a_real_tool" in results[0].message
        assert results[0].section == SECTION_TOOLS

    def test_all_registered_is_ok(self) -> None:
        with (
            patch(
                "openjarvis.core.health._get_config",
                return_value=_config("calculator,think"),
            ),
            patch(
                "openjarvis.core.registry.ToolRegistry.keys",
                return_value=["calculator", "think", "extra"],
            ),
        ):
            results = _check_configured_tools_registered()
        assert results[0].status == "ok"

    def test_no_tools_enabled_is_a_warning(self) -> None:
        with patch("openjarvis.core.health._get_config", return_value=_config("")):
            results = _check_configured_tools_registered()
        assert results[0].status == "warn"


class TestToolModulesImport:
    def test_the_real_package_imports_cleanly(self) -> None:
        # Verified by planting a deliberately broken module: this check
        # reported "Will not import: zz_health_probe (ModuleNotFoundError)".
        results = _check_tool_modules_import()
        assert results[0].status == "ok"

    def test_a_broken_module_is_reported(self, tmp_path) -> None:
        broken = tmp_path / "brokentool.py"
        broken.write_text("import nope_not_here\n", encoding="utf-8")
        def _import(name):
            if name.endswith("brokentool"):
                raise ModuleNotFoundError("No module named 'nope_not_here'")
            return SimpleNamespace()

        # Patch __file__, not sys.modules: `import openjarvis.tools as x`
        # reads the attribute already bound on the parent package, so a
        # sys.modules patch is ignored once the real package has been
        # imported by an earlier test.
        with (
            patch("openjarvis.tools.__file__", str(tmp_path / "__init__.py")),
            patch("importlib.import_module", side_effect=_import),
        ):
            results = _check_tool_modules_import()
        assert results[0].status == "fail"
        assert "brokentool" in results[0].message


class TestToolDirectories:
    def test_an_unset_allowlist_on_an_enabled_tool_fails(self) -> None:
        # Fail-closed: the tool is offered, accepts the call, and can reach
        # nothing at all.
        with (
            patch(
                "openjarvis.core.health._get_config",
                return_value=_config("git_status"),
            ),
            patch.dict("os.environ", {"OPENJARVIS_GIT_DIRS": ""}, clear=False),
        ):
            results = _check_tool_directories()
        assert results[0].status == "fail"
        assert "OPENJARVIS_GIT_DIRS" in (results[0].details or "")

    def test_a_set_allowlist_is_ok(self, tmp_path) -> None:
        with (
            patch(
                "openjarvis.core.health._get_config",
                return_value=_config("git_status"),
            ),
            patch.dict(
                "os.environ", {"OPENJARVIS_GIT_DIRS": str(tmp_path)}, clear=False
            ),
        ):
            results = _check_tool_directories()
        assert results[0].status == "ok"

    def test_a_missing_directory_is_a_warning(self, tmp_path) -> None:
        absent = tmp_path / "gone"
        with (
            patch(
                "openjarvis.core.health._get_config",
                return_value=_config("git_status"),
            ),
            patch.dict(
                "os.environ", {"OPENJARVIS_GIT_DIRS": str(absent)}, clear=False
            ),
        ):
            results = _check_tool_directories()
        assert results[0].status == "warn"

    def test_find_file_is_not_treated_as_fail_closed(self) -> None:
        # find_file falls back to sensible defaults when its variable is
        # unset. Judging it by the fail-closed rule would report a working
        # tool as broken, which is the noise that makes a health page useless.
        with (
            patch(
                "openjarvis.core.health._get_config",
                return_value=_config("find_file"),
            ),
            patch.dict(
                "os.environ", {"OPENJARVIS_FIND_FILE_DIRS": ""}, clear=False
            ),
        ):
            assert _check_tool_directories() == []
