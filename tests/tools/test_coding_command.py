"""Tests for the restricted coding command tool."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from openjarvis.tools.coding_command import CodingCommandTool


class TestCodingCommandTool:
    def test_fails_closed_without_allowed_dirs(self, tmp_path):
        result = CodingCommandTool(allowed_dirs=[]).execute(
            executable="python",
            cwd=str(tmp_path),
        )

        assert result.success is False
        assert "Access denied" in result.content

    def test_allowed_cwd_uses_argument_array_and_no_shell(self, tmp_path):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok\n", stderr=""
        )
        with (
            patch(
                "openjarvis.tools.coding_command.shutil.which",
                return_value="python",
            ),
            patch(
                "openjarvis.tools.coding_command.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            result = CodingCommandTool([str(tmp_path)]).execute(
                executable="python",
                args=["-c", "print('ok')"],
                cwd=str(tmp_path),
            )

        assert result.success is True
        assert result.content == "ok\n"
        command = run.call_args.args[0]
        assert command == ["python", "-c", "print('ok')"]
        assert run.call_args.kwargs["shell"] is False
        assert run.call_args.kwargs["cwd"] == str(tmp_path.resolve())
        assert set(run.call_args.kwargs["env"]).issubset(
            {"PATH", "PATHEXT", "SystemRoot", "WINDIR", "TEMP", "TMP"}
        )

    def test_denies_cwd_outside_allowed_root(self, tmp_path):
        allowed = tmp_path / "allowed"
        denied = tmp_path / "denied"
        allowed.mkdir()
        denied.mkdir()

        result = CodingCommandTool([str(allowed)]).execute(
            executable="python",
            cwd=str(denied),
        )

        assert result.success is False
        assert "Access denied" in result.content

    def test_denies_traversal_outside_allowed_root(self, tmp_path):
        allowed = tmp_path / "allowed"
        allowed.mkdir()

        result = CodingCommandTool([str(allowed)]).execute(
            executable="python",
            cwd=str(allowed / ".."),
        )

        assert result.success is False
        assert "Access denied" in result.content

    def test_denies_unlisted_executable(self, tmp_path):
        result = CodingCommandTool([str(tmp_path)]).execute(
            executable="powershell",
            cwd=str(tmp_path),
        )

        assert result.success is False
        assert result.content == "Executable denied: powershell"

    def test_denies_executable_path(self, tmp_path):
        result = CodingCommandTool([str(tmp_path)]).execute(
            executable=str(tmp_path / "python.exe"),
            cwd=str(tmp_path),
        )

        assert result.success is False
        assert result.content == "Executable paths are not allowed."

    def test_reports_timeout(self, tmp_path):
        with (
            patch(
                "openjarvis.tools.coding_command.shutil.which",
                return_value="python",
            ),
            patch(
                "openjarvis.tools.coding_command.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["python"], 1),
            ),
        ):
            result = CodingCommandTool([str(tmp_path)]).execute(
                executable="python",
                args=["-c", "pass"],
                cwd=str(tmp_path),
                timeout_seconds=1,
            )

        assert result.success is False
        assert result.content == "Command timed out after 1 seconds."
        assert result.metadata == {"timeout_seconds": 1}
