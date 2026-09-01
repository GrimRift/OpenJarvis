"""Find a file the user has lost, and open Explorer at it so they can take over.

The real request this serves: "I saved a Revit file somewhere and I cannot
find it." Windows' own search is slow and often silently misses anything
outside the index, so this walks the disk itself, in the order a person would
look: the folders where things actually land first, then everywhere else.

The handover matters as much as the finding. Sage does not try to *work on*
the file — it reveals the file selected in Explorer and gets out of the way,
which is the point at which the user is better at this than Sage is.

**Scope note.** This reads file *names* across the fixed drives; it never
reads file contents, and it returns paths, not data. The walk is bounded by a
time budget rather than running to completion, because a full pass over two
large drives takes minutes and a search that never returns is a search nobody
uses.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

#: Looked at first, in this order. These are where downloads and saves land on
#: this machine, so the common case never reaches the full walk at all.
_DEFAULT_PRIORITY_DIRS = (
    r"D:\Downloads",
    r"C:\Users\yanso\Downloads",
    r"C:\Users\yanso\Grim",
)

#: Searched after the priority list and before the rest of the drives.
_USER_SUBDIRS = (
    "Desktop",
    "Documents",
    "Pictures",
    "Videos",
    "Music",
    "OneDrive",
)

#: Directories with nothing a person ever loses in them. Skipping these is
#: most of the reason the walk finishes inside its budget.
_SKIP_NAMES = frozenset(
    {
        "$recycle.bin",
        ".git",
        "__pycache__",
        "node_modules",
        "system volume information",
        "windows",
        "windows.old",
        "winsxs",
        "program files",
        "program files (x86)",
        "programdata",
        "temp",
        "tmp",
        "cache",
        "caches",
        "packages",
        "venv",
        ".venv",
        "site-packages",
    }
)

#: Refused by ``open``. Revealing one of these in Explorer is fine; launching
#: it is running a program the user has not looked at yet.
_NEVER_OPEN = frozenset(
    {
        ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".ps1", ".psm1",
        ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".cpl",
        ".reg", ".jar", ".msc", ".lnk", ".pif", ".sys", ".dll",
    }
)

#: A walk that runs longer than this is worse than one that reports what it
#: found so far and says it did not finish.
_DEFAULT_BUDGET_SECONDS = 25.0

#: Cap on the first phase so the full-drive walk always gets a turn.
_PRIORITY_BUDGET_SECONDS = 8.0

_MAX_RESULTS = 200


@dataclass(frozen=True)
class Hit:
    path: str
    size: int
    modified: float

    def describe(self) -> str:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.modified))
        return f"{self.path}  ({_human_size(self.size)}, modified {when})"


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def priority_dirs() -> List[str]:
    configured = os.environ.get("OPENJARVIS_FIND_FILE_DIRS")
    if configured:
        return [part for part in configured.split(os.pathsep) if part.strip()]
    return list(_DEFAULT_PRIORITY_DIRS)


def _matcher(query: str, extension: str):
    """Return a predicate over filenames.

    A query containing a wildcard is treated as a glob, because someone who
    types ``*.rvt`` means it. Everything else is a case-insensitive substring,
    which is what people actually remember about a filename.
    """
    wanted = (query or "").strip().lower()
    suffix = (extension or "").strip().lower()
    if suffix and not suffix.startswith("."):
        suffix = "." + suffix
    is_glob = any(character in wanted for character in "*?[")

    def matches(name: str) -> bool:
        lowered = name.lower()
        if suffix and not lowered.endswith(suffix):
            return False
        if not wanted:
            return bool(suffix)
        if is_glob:
            return fnmatch.fnmatch(lowered, wanted)
        return wanted in lowered

    return matches


def _fixed_drives() -> List[str]:
    import ctypes

    drives = []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for offset in range(26):
        if not mask & (1 << offset):
            continue
        root = f"{chr(ord('A') + offset)}:\\"
        # 3 == DRIVE_FIXED. Network and removable drives make the walk
        # unpredictable and are not where someone loses a local save.
        if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:
            drives.append(root)
    return drives


def _scan(
    root: str,
    matches,
    *,
    deadline: float,
    seen: set,
    results: List[Hit],
    skip_noise: bool,
) -> bool:
    """Walk *root*. Returns False if the time budget ran out."""
    stack = [root]
    while stack:
        if time.monotonic() > deadline or len(results) >= _MAX_RESULTS:
            return time.monotonic() <= deadline
        current = stack.pop()
        key = os.path.normcase(os.path.abspath(current))
        if key in seen:
            continue
        seen.add(key)
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if skip_noise and entry.name.lower() in _SKIP_NAMES:
                                continue
                            stack.append(entry.path)
                        elif matches(entry.name):
                            info = entry.stat()
                            results.append(
                                Hit(entry.path, info.st_size, info.st_mtime)
                            )
                    except OSError:
                        continue
        except (OSError, PermissionError):
            continue
    return True


def search(
    query: str,
    *,
    extension: str = "",
    budget_seconds: float = _DEFAULT_BUDGET_SECONDS,
) -> Tuple[List[Hit], bool]:
    """Find files matching *query*. Returns ``(hits, finished)``.

    Newest first: a file someone has just lost track of is nearly always a
    recent one, and "the screenshot I took earlier" has no other handle on it.
    """
    matches = _matcher(query, extension)
    total = max(1.0, float(budget_seconds))
    started = time.monotonic()
    seen: set = set()
    results: List[Hit] = []

    # The two phases get separate budgets. Sharing one deadline meant a query
    # with no match spent the entire allowance inside D:\Downloads and the
    # full-drive fallback — the half that answers "it could be anywhere" —
    # never ran at all. Measured: 25s in, still in phase one.
    priority_deadline = started + min(_PRIORITY_BUDGET_SECONDS, total * 0.4)
    overall_deadline = started + total

    home = os.path.expanduser("~")
    roots: List[Tuple[str, bool]] = [(path, False) for path in priority_dirs()]
    roots += [(os.path.join(home, name), False) for name in _USER_SUBDIRS]

    for root, skip_noise in roots:
        if not os.path.isdir(root):
            continue
        if not _scan(
            root,
            matches,
            deadline=priority_deadline,
            seen=seen,
            results=results,
            skip_noise=skip_noise,
        ):
            break

    finished = True
    if not results:
        for drive in _fixed_drives():
            finished = _scan(
                drive,
                matches,
                deadline=overall_deadline,
                seen=seen,
                results=results,
                skip_noise=True,
            )
            if not finished:
                break

    results.sort(key=lambda hit: hit.modified, reverse=True)
    return results, finished


def reveal(path: str) -> None:
    """Open Explorer with *path* selected.

    A command *string*, not an argument list, and this is not carelessness.
    Explorer re-parses its own command line and wants the quotes around the
    path alone — ``/select,"C:\\a b\\c.png"``. Handed a list, Python quotes the
    whole token as ``"/select,C:\\a b\\c.png"``, which Explorer cannot read: it
    silently ignored the argument and opened at Documents instead of the
    folder holding the file. Caught live, on a screenshot whose name contains
    spaces — which is every screenshot Windows takes.

    ``shell=False`` still holds, so no shell interprets this; on Windows a
    string goes to CreateProcess verbatim. The path is one Sage found on disk
    itself, never user- or model-authored text.
    """
    target = os.path.abspath(path)
    subprocess.Popen(f'explorer.exe /select,"{target}"', shell=False, close_fds=True)


@ToolRegistry.register("find_file")
class FindFileTool(BaseTool):
    """Locate a file anywhere on the machine and reveal it in Explorer."""

    tool_id = "find_file"
    is_local = True

    def __init__(self, allowed_dirs: Optional[List[str]] = None) -> None:
        super().__init__()
        self._priority = allowed_dirs or priority_dirs()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="find_file",
            description=(
                "Find a file by name anywhere on the user's drives when they "
                "cannot remember where they saved it, then open File Explorer "
                "with it selected so they can take over. Works for any file "
                "type. Give the part of the name they remember; add an "
                "extension like 'rvt' or 'png' to narrow it. Searches "
                "Downloads and their user folders first, then the whole disk."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Part of the filename. Wildcards like '*.rvt' work."
                        ),
                    },
                    "extension": {
                        "type": "string",
                        "description": "Optional file extension, e.g. 'rvt', 'png'.",
                    },
                    "reveal": {
                        "type": "boolean",
                        "description": (
                            "Open Explorer at the best match. Default true."
                        ),
                    },
                    "open": {
                        "type": "boolean",
                        "description": (
                            "Also open the file in its default application. "
                            "Default false. Refused for programs and scripts."
                        ),
                    },
                },
                "required": ["query"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        query = str(params.get("query") or "").strip()
        extension = str(params.get("extension") or "").strip()
        should_reveal = params.get("reveal", True)
        should_open = bool(params.get("open", False))
        if not query and not extension:
            return self._fail("Give me part of the filename to look for.")

        try:
            hits, finished = search(query, extension=extension)
        except Exception as error:  # pragma: no cover — filesystem surface
            return self._fail(f"the search failed: {error}")

        if not hits:
            where = "everywhere I can reach" if finished else "the likely folders"
            tail = "" if finished else " before the time limit"
            return ToolResult(
                tool_name=self.tool_id,
                content=(
                    f"No file matching {query or extension!r} in {where}{tail}. "
                    "A different spelling or extension may help."
                ),
                success=False,
                metadata={"finished": finished, "count": 0},
            )

        best = hits[0]
        lines = [f"Found {len(hits)} match(es), newest first:"]
        lines += [f"  {hit.describe()}" for hit in hits[:10]]
        if len(hits) > 10:
            lines.append(f"  ... and {len(hits) - 10} more")
        if not finished:
            lines.append("(stopped at the time limit — there may be more)")

        actions = []
        if should_reveal:
            try:
                reveal(best.path)
                actions.append("opened Explorer with it selected")
            except Exception as error:
                actions.append(f"could not open Explorer: {error}")
        if should_open:
            suffix = os.path.splitext(best.path)[1].lower()
            if suffix in _NEVER_OPEN:
                actions.append(
                    f"did not launch it — {suffix} is a program, not a document"
                )
            else:
                try:
                    os.startfile(best.path)  # noqa: S606 — user asked to open it
                    actions.append("opened it in its default application")
                except Exception as error:
                    actions.append(f"could not open it: {error}")
        if actions:
            lines.append(f"For the newest match I {', and '.join(actions)}.")

        return ToolResult(
            tool_name=self.tool_id,
            content="\n".join(lines),
            success=True,
            metadata={
                "count": len(hits),
                "finished": finished,
                "best": best.path,
                "paths": [hit.path for hit in hits[:_MAX_RESULTS]],
            },
        )

    def _fail(self, reason: str) -> ToolResult:
        return ToolResult(tool_name=self.tool_id, content=reason, success=False)


__all__ = ["Hit", "priority_dirs", "reveal", "search"]
