"""Directory listing tool — safely enumerate approved filesystem roots."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.security.file_policy import is_sensitive_file
from openjarvis.tools._stubs import BaseTool, ToolSpec


_MAX_ENTRIES = 500

# These may be shown in a directory listing, but we avoid recursively
# descending into large/generated directories by default.
_SKIP_RECURSE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


@ToolRegistry.register("directory_list")
class DirectoryListTool(BaseTool):
    """List files and directories within explicitly allowed read roots."""

    tool_id = "directory_list"

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        if allowed_dirs is None:
            configured = os.environ.get("OPENJARVIS_FILE_READ_DIRS", "")
            allowed_dirs = [
                entry.strip()
                for entry in configured.split(os.pathsep)
                if entry.strip()
            ]

        self._allowed_dirs = [Path(d).resolve() for d in allowed_dirs]

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="directory_list",
            description=(
                "List files and directories inside approved filesystem roots. "
                "Can optionally recurse to a limited depth."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list.",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "Recursively list subdirectories. Default: false.",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": (
                            "Maximum recursion depth when recursive=true. "
                            "Default: 2, maximum: 5."
                        ),
                    },
                    "include_hidden": {
                        "type": "boolean",
                        "description": (
                            "Include hidden/dot-prefixed entries. "
                            "Sensitive files remain filtered. Default: false."
                        ),
                    },
                },
                "required": ["path"],
            },
            category="filesystem",
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

    def execute(self, **params: Any) -> ToolResult:
        raw_path = params.get("path", "")
        if not raw_path:
            return ToolResult(
                tool_name="directory_list",
                content="No path provided.",
                success=False,
            )

        path = Path(raw_path)

        # Check the security boundary before revealing whether the path exists.
        if not self._is_path_allowed(path):
            return ToolResult(
                tool_name="directory_list",
                content=f"Access denied: {raw_path} is outside allowed directories.",
                success=False,
            )

        if not path.exists():
            return ToolResult(
                tool_name="directory_list",
                content=f"Directory not found: {raw_path}",
                success=False,
            )

        if not path.is_dir():
            return ToolResult(
                tool_name="directory_list",
                content=f"Not a directory: {raw_path}",
                success=False,
            )

        recursive = bool(params.get("recursive", False))
        include_hidden = bool(params.get("include_hidden", False))

        try:
            max_depth = int(params.get("max_depth", 2))
        except (TypeError, ValueError):
            return ToolResult(
                tool_name="directory_list",
                content="max_depth must be an integer.",
                success=False,
            )

        if max_depth < 1 or max_depth > 5:
            return ToolResult(
                tool_name="directory_list",
                content="max_depth must be between 1 and 5.",
                success=False,
            )

        base = path.resolve()
        output: list[str] = []
        truncated = False

        def should_hide(entry: Path) -> bool:
            if not include_hidden and entry.name.startswith("."):
                return True

            if is_sensitive_file(entry):
                return True

            # Prevent symlinks/junctions from escaping an approved root.
            if not self._is_path_allowed(entry):
                return True

            return False

        def walk(current: Path, depth: int) -> None:
            nonlocal truncated

            if truncated:
                return

            try:
                children = sorted(
                    current.iterdir(),
                    key=lambda item: item.name.lower(),
                )
            except (OSError, PermissionError):
                return

            for child in children:
                if len(output) >= _MAX_ENTRIES:
                    truncated = True
                    return

                if should_hide(child):
                    continue

                try:
                    relative = child.relative_to(base)
                except ValueError:
                    continue

                if child.is_symlink():
                    output.append(f"[L] {relative}")
                    continue

                try:
                    if child.is_dir():
                        output.append(f"[D] {relative}")

                        if (
                            recursive
                            and depth < max_depth
                            and child.name not in _SKIP_RECURSE_DIRS
                        ):
                            walk(child, depth + 1)

                    elif child.is_file():
                        output.append(f"[F] {relative}")

                except (OSError, PermissionError):
                    continue

        walk(base, 1)

        if not output:
            body = "(empty directory)"
        else:
            body = "\n".join(output)

        if truncated:
            body += f"\n... truncated after {_MAX_ENTRIES} entries"

        return ToolResult(
            tool_name="directory_list",
            content=f"Directory: {base}\n{body}",
            success=True,
            metadata={
                "path": str(base),
                "entries_returned": len(output),
                "recursive": recursive,
                "max_depth": max_depth,
                "truncated": truncated,
            },
        )


__all__ = ["DirectoryListTool"]