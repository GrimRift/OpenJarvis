"""Restricted Windows-native command runner for coding tasks."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_ALLOWED_EXECUTABLES = frozenset(
    {"python", "pytest", "uv", "git", "node", "npm", "npx"}
)
_DEFAULT_TIMEOUT_SECONDS = 30
_MAX_TIMEOUT_SECONDS = 120
_MAX_OUTPUT_BYTES = 50 * 1024
_MINIMAL_ENV_KEYS = (
    "PATH",
    "PATHEXT",
    "SystemRoot",
    "WINDIR",
    "TEMP",
    "TMP",
)


def _truncate(text: str) -> str:
    if len(text.encode("utf-8", errors="replace")) <= _MAX_OUTPUT_BYTES:
        return text
    return text[:_MAX_OUTPUT_BYTES] + "\n... (output truncated)"


@ToolRegistry.register("coding_command")
class CodingCommandTool(BaseTool):
    """Run an allowlisted coding executable inside an approved project root."""

    tool_id = "coding_command"

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        if allowed_dirs is None:
            configured = os.environ.get("OPENJARVIS_CODING_DIRS", "")
            allowed_dirs = [
                entry.strip()
                for entry in configured.split(os.pathsep)
                if entry.strip()
            ]

        self._allowed_dirs = [Path(directory).resolve() for directory in allowed_dirs]

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="coding_command",
            description=(
                "Run one approved coding executable directly inside an approved "
                "project directory. No shell syntax is supported."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "executable": {
                        "type": "string",
                        "description": (
                            "Executable name: python, pytest, uv, git, node, npm, "
                            "or npx. Paths are not accepted."
                        ),
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Argument array. Default: empty array.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": (
                            "Working directory inside an approved project root."
                        ),
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Timeout from 1 to 120 seconds. Default: 30.",
                    },
                },
                "required": ["executable", "cwd"],
            },
            category="code",
            required_capabilities=["code:execute"],
            timeout_seconds=float(_MAX_TIMEOUT_SECONDS + 5),
        )

    def _is_path_allowed(self, path: Path) -> bool:
        if not self._allowed_dirs:
            return False

        try:
            resolved = path.resolve()
        except OSError:
            return False

        return any(
            resolved == allowed_dir or allowed_dir in resolved.parents
            for allowed_dir in self._allowed_dirs
        )

    @staticmethod
    def _minimal_environment() -> dict[str, str]:
        return {
            key: os.environ[key]
            for key in _MINIMAL_ENV_KEYS
            if key in os.environ
        }

    def execute(self, **params: Any) -> ToolResult:
        raw_cwd = params.get("cwd", "")
        if not isinstance(raw_cwd, str) or not raw_cwd:
            return self._error("No working directory provided.")

        cwd = Path(raw_cwd)
        if not self._is_path_allowed(cwd):
            return self._error(
                f"Access denied: {raw_cwd} is outside allowed directories."
            )
        if not cwd.exists() or not cwd.is_dir():
            return self._error(f"Invalid working directory: {raw_cwd}")
        resolved_cwd = cwd.resolve()

        executable = params.get("executable", "")
        if not isinstance(executable, str) or not executable:
            return self._error("No executable provided.")
        if Path(executable).name != executable:
            return self._error("Executable paths are not allowed.")

        executable_name = executable.lower()
        if executable_name not in _ALLOWED_EXECUTABLES:
            return self._error(f"Executable denied: {executable}")

        args = params.get("args", [])
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            return self._error("args must be an array of strings.")
        if any("\x00" in arg for arg in args):
            return self._error("Arguments may not contain NUL characters.")

        try:
            timeout_seconds = int(
                params.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
            )
        except (TypeError, ValueError):
            return self._error("timeout_seconds must be an integer.")
        if timeout_seconds < 1 or timeout_seconds > _MAX_TIMEOUT_SECONDS:
            return self._error("timeout_seconds must be between 1 and 120.")

        executable_path = shutil.which(executable_name)
        if executable_path is None:
            return self._error(f"Executable not found on PATH: {executable_name}")

        command = [executable_path, *args]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                cwd=str(resolved_cwd),
                env=self._minimal_environment(),
                timeout=timeout_seconds,
                shell=False,
            )
        except FileNotFoundError:
            return self._error(f"Executable not found: {executable_name}")
        except OSError as exc:
            return self._error(f"Command could not start: {exc}")
        except subprocess.TimeoutExpired as exc:
            partial = ""
            if exc.stdout:
                partial += str(exc.stdout)
            if exc.stderr:
                partial += ("\n" if partial else "") + str(exc.stderr)
            message = f"Command timed out after {timeout_seconds} seconds."
            if partial:
                message += "\n" + _truncate(partial)
            return self._error(message, metadata={"timeout_seconds": timeout_seconds})

        output = result.stdout
        if result.stderr:
            output += ("\n" if output else "") + result.stderr

        return ToolResult(
            tool_name="coding_command",
            content=_truncate(output) or "(no output)",
            success=result.returncode == 0,
            metadata={
                "returncode": result.returncode,
                "executable": executable_name,
                "cwd": str(resolved_cwd),
            },
        )

    @staticmethod
    def _error(
        content: str,
        *,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ToolResult:
        return ToolResult(
            tool_name="coding_command",
            content=content,
            success=False,
            metadata=metadata or {},
        )


__all__ = ["CodingCommandTool"]
