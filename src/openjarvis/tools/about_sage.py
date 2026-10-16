"""Sage answering questions about itself from its own code and settings.

Asked "what can you do?", Sage used to answer from whichever tool schemas the
router happened to send that turn, plus a guess. Every part of this tool reads
the thing it describes instead: the tool registry and the enabled list in
``config.toml``, the live settings files, the app's own settings (pushed by
the app, since they only exist in the page), the guide in ``docs/``, the git
history and the tracked source files of the checkout that is running.

Read-only. ``code`` reads only files git tracks under ``SOURCE_ROOTS`` and
never a sensitive file, so keys and the data folder cannot be reached
through it.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import tomllib

from openjarvis.core.paths import get_config_dir
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.security.file_policy import is_sensitive_file
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

#: The checkout this code runs from: live reads live, staging reads staging.
REPO_ROOT = Path(__file__).resolve().parents[3]
GUIDE_PATH = REPO_ROOT / "docs" / "sage-guide.md"
SOURCE_ROOTS = ("src/openjarvis", "frontend/src", "docs/sage-guide.md")
#: Built bundles are tracked too, and one line of them is a whole file.
_SKIP_PREFIXES = ("src/openjarvis/server/static/",)
_CODE_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".css",
    ".md",
    ".toml",
    ".json",
    ".rs",
    ".html",
}
SNAPSHOT_NAME = "app_settings.json"
_SECRET_KEY = re.compile(r"key|token|secret|password|credential|chat_id", re.I)
#: Left out of the settings answer: the prompt is long and not a setting,
#: and the tool list is what ``abilities`` reports.
_SKIP_CONFIG_KEYS = {"system_prompt", "tools"}

MAX_SEARCH_HITS = 40
MAX_READ_LINES = 300
MAX_CHANGES = 40

_files_lock = threading.Lock()
_files_cache: Optional[Tuple[float, List[str]]] = None
_FILES_TTL = 300.0


# -- the app's settings, pushed by the page ----------------------------------


def snapshot_path(config_dir: Optional[Path] = None) -> Path:
    return (config_dir or get_config_dir()) / SNAPSHOT_NAME


def clean_app_settings(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Keep plain values only, and never anything that names a secret."""
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or _SECRET_KEY.search(key):
            continue
        if key.lower() in {"apiurl"}:
            continue
        if isinstance(value, (bool, int, float)) or value is None:
            out[key] = value
        elif isinstance(value, str):
            out[key] = value[:200]
    return out


def save_app_settings(raw: Dict[str, Any], config_dir: Optional[Path] = None) -> int:
    settings = clean_app_settings(raw)
    path = snapshot_path(config_dir)
    payload = {"saved_at": time.time(), "settings": settings}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return len(settings)


def load_app_settings(config_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(snapshot_path(config_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# -- helpers ------------------------------------------------------------------


def _git(*args: str, timeout: float = 10.0) -> Optional[str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("about_sage: git %s failed: %s", args[0], exc)
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _read_config(config_dir: Optional[Path] = None) -> Dict[str, Any]:
    try:
        with open((config_dir or get_config_dir()) / "config.toml", "rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _enabled_tools(cfg: Dict[str, Any]) -> List[str]:
    raw = (cfg.get("agent") or {}).get("tools") or ""
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return [t.strip() for t in str(raw).split(",") if t.strip()]


def _first_sentence(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    line = match.group(1) if match else text
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _tool_summary(name: str) -> str:
    try:
        cls = ToolRegistry.get(name)
    except Exception:  # noqa: BLE001
        return ""
    try:
        return _first_sentence(cls().spec.description)
    except Exception:  # noqa: BLE001 -- some tools need constructor arguments
        return _first_sentence((cls.__doc__ or "").strip())


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2]


def _flatten(prefix: str, value: Any, out: List[Tuple[str, Any]]) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            if key in _SKIP_CONFIG_KEYS:
                continue
            _flatten(f"{prefix}.{key}" if prefix else str(key), inner, out)
    else:
        out.append((prefix, value))


def source_files() -> List[str]:
    """Tracked files ``code`` may read, cached for a few minutes."""
    global _files_cache
    with _files_lock:
        now = time.monotonic()
        if _files_cache and now - _files_cache[0] < _FILES_TTL:
            return _files_cache[1]
        listed = _git("ls-files", "--", *SOURCE_ROOTS) or ""
        files = [
            f
            for f in listed.splitlines()
            if f
            and not f.startswith(_SKIP_PREFIXES)
            and Path(f).suffix.lower() in _CODE_SUFFIXES
            and not is_sensitive_file(f)
        ]
        _files_cache = (now, files)
        return files


# -- the parts ----------------------------------------------------------------


def overview(config_dir: Optional[Path] = None) -> str:
    cfg = _read_config(config_dir)
    lines = [
        "Sage: a personal assistant built on a customised OpenJarvis framework, "
        "running on this PC (server + desktop app)."
    ]
    head = _git("log", "-1", "--format=%h|%cd|%s", "--date=format:%Y-%m-%d %H:%M")
    branch = (_git("branch", "--show-current") or "").strip()
    if head:
        sha, date, subject = (head.strip().split("|", 2) + ["", ""])[:3]
        lines.append(
            f"Version: commit {sha} on {branch or 'detached'}, {date} -- {subject}"
        )
    engine = (cfg.get("engine") or {}).get("default", "?")
    local_model = (cfg.get("intelligence") or {}).get("default_model", "?")
    try:
        from openjarvis.core.model_preference import load_preference

        pref = load_preference(config_dir)
        if pref.prefer_cloud:
            lines.append(
                f"Answering model: cloud ({pref.cloud_model}), falling back to "
                f"the local {local_model} on {engine}."
            )
        else:
            lines.append(f"Answering model: local only ({local_model} on {engine}).")
    except Exception:  # noqa: BLE001
        lines.append(f"Local model: {local_model} on {engine}.")
    enabled = _enabled_tools(cfg)
    lines.append(
        f"Tools switched on: {len(enabled)} (ask for 'abilities' for the list)."
    )
    lines.append("Other parts: 'abilities', 'settings', 'how_to', 'whats_new', 'code'.")
    return "\n".join(lines)


def abilities(query: str = "", config_dir: Optional[Path] = None) -> str:
    enabled = _enabled_tools(_read_config(config_dir))
    registered = set(ToolRegistry.keys())
    words = _words(query)
    rows = []
    for name in enabled:
        if name not in registered:
            continue
        summary = _tool_summary(name)
        if words and not any(w in f"{name} {summary}".lower() for w in words):
            continue
        rows.append(f"- {name}: {summary}")
    off = sorted(registered - set(enabled))
    head = (
        f"Tools switched on matching '{query}' ({len(rows)}):"
        if words
        else f"Tools switched on ({len(rows)}):"
    )
    if words and not rows:
        head = (
            f"No switched-on tool matches '{query}'. It may still be a "
            "feature of the app itself: try how_to."
        )
    out = [head, *rows]
    if not words:
        out.append(
            "Also part of Sage without a tool: voice (wake word 'Hey Sage', "
            "talking over answers), the morning brief, reminders it starts "
            "itself, memory of facts, chat rewind, the Memory Brain, "
            "audiobooks, the Health page. Details: how_to."
        )
        out.append(f"Built in but switched off ({len(off)}): {', '.join(off)}")
    return "\n".join(out)


#: What the user calls a setting -> words in its key.
_SETTING_ALIASES = {
    "dip": ["duck"],
    "duck": ["duck"],
    "volume": ["duck", "gain", "boost"],
    "voice": ["voice", "tts"],
    "microphone": ["mic"],
    "listen": ["listen", "barge"],
    "interrupt": ["barge"],
    "theme": ["theme"],
    "orb": ["orb"],
}


def settings(query: str = "", config_dir: Optional[Path] = None) -> str:
    words = _words(query)
    for word in list(words):
        words += _SETTING_ALIASES.get(word, [])
    pairs: List[Tuple[str, Any]] = []
    _flatten("", _read_config(config_dir), pairs)
    server = [
        f"- {key} = " + ("(set, hidden)" if _SECRET_KEY.search(key) else repr(value))
        for key, value in pairs
        if not words or any(w in key.lower() for w in words)
    ]
    out = ["Server settings (config.toml):", *(server or ["- (none match)"])]
    snap = load_app_settings(config_dir)
    if snap and isinstance(snap.get("settings"), dict):
        age = time.strftime("%Y-%m-%d %H:%M", time.localtime(snap.get("saved_at") or 0))
        app = [
            f"- {key} = {value!r}"
            for key, value in sorted(snap["settings"].items())
            if not words or any(w in key.lower() for w in words)
        ]
        out += [
            f"App settings (Settings page, as of {age}):",
            *(app or ["- (none match)"]),
        ]
    else:
        out.append("App settings: the app has not reported them yet.")
    return "\n".join(out)


def how_to(query: str = "") -> str:
    try:
        text = GUIDE_PATH.read_text(encoding="utf-8")
    except OSError:
        return "The guide (docs/sage-guide.md) is missing from this install."
    sections = re.split(r"(?m)^(?=## )", text)
    intro, parts = sections[0], sections[1:]
    words = _words(query)
    if not words:
        titles = [p.splitlines()[0][3:].strip() for p in parts]
        return intro.strip() + "\nSections: " + "; ".join(titles)
    scored = []
    for part in parts:
        low = part.lower()
        title = part.splitlines()[0].lower()
        score = sum(low.count(w) + 3 * title.count(w) for w in words)
        if score:
            scored.append((score, part.strip()))
    if not scored:
        return f"The guide has nothing on '{query}'. Try abilities or code."
    scored.sort(key=lambda s: -s[0])
    return "\n\n".join(part for _, part in scored[:2])


def whats_new(days: int = 14) -> str:
    days = max(1, min(int(days or 14), 90))
    log = _git(
        "log",
        f"--since={days}.days",
        "--no-merges",
        f"-{MAX_CHANGES}",
        "--date=format:%Y-%m-%d",
        "--format=%h\x1f%ad\x1f%s\x1f%b\x1e",
    )
    if log is None:
        return "Could not read the change history (git unavailable)."
    out = []
    for entry in log.split("\x1e"):
        fields = entry.strip().split("\x1f")
        if len(fields) < 3:
            continue
        sha, date, subject = fields[0], fields[1], fields[2]
        subject = re.sub(r"^[\w./-]+:\s*", "", subject)
        body = fields[3].strip().split("\n\n")[0] if len(fields) > 3 else ""
        body = " ".join(body.split())
        body = re.sub(r"Co-Authored-By:.*", "", body).strip()
        line = f"- {date} {sha}: {subject}"
        if body:
            line += f" -- {body[:300]}"
        out.append(line)
    if not out:
        return f"No changes in the last {days} days."
    return (
        f"Changes in the last {days} days, newest first ({len(out)}). "
        "Say them in plain words, not commit jargon:\n" + "\n".join(out)
    )


def code(query: str = "", path: str = "", start_line: int = 1) -> str:
    files = source_files()
    if path:
        wanted = path.replace("\\", "/").strip().lstrip("./")
        matches = [f for f in files if f == wanted or f.endswith("/" + wanted)]
        if not matches:
            return (
                f"'{path}' is not one of Sage's readable source files. "
                "Search with a query first to find the path."
            )
        rel = matches[0]
        lines = (
            (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines()
        )
        start = max(1, int(start_line or 1))
        chunk = lines[start - 1 : start - 1 + MAX_READ_LINES]
        body = "\n".join(f"{start + i}: {line}" for i, line in enumerate(chunk))
        more = start - 1 + MAX_READ_LINES < len(lines)
        tail = (
            f"\n(more: continue with start_line={start + MAX_READ_LINES})"
            if more
            else ""
        )
        end = start + len(chunk) - 1
        return f"{rel} (lines {start}-{end} of {len(lines)}):\n{body}{tail}"
    if not query.strip():
        return "Give a query to search Sage's code, or a path to read."
    needle = query.strip().lower()
    words = _words(query)
    hits: List[str] = []
    word_hits: List[str] = []
    for rel in files:
        try:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        if needle not in low and not (words and all(w in low for w in words)):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            ll = line.lower()
            if needle in ll:
                hits.append(f"{rel}:{number}: {line.strip()[:200]}")
            elif words and len(words) > 1 and sum(w in ll for w in words) >= 2:
                word_hits.append(f"{rel}:{number}: {line.strip()[:200]}")
            if len(hits) >= MAX_SEARCH_HITS:
                break
        if len(hits) >= MAX_SEARCH_HITS:
            break
    found = (hits or word_hits)[:MAX_SEARCH_HITS]
    if not found:
        return f"Nothing in Sage's code matches '{query}'."
    return (
        f"Matches for '{query}' ({len(found)} shown). Read one with "
        "part='code' and its path:\n" + "\n".join(found)
    )


_PARTS = ("overview", "abilities", "settings", "how_to", "whats_new", "code")


@ToolRegistry.register("about_sage")
class AboutSageTool(BaseTool):
    """Answer questions about Sage itself from its own code and settings."""

    tool_id = "about_sage"
    is_local = True

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="about_sage",
            description=(
                "Facts about Sage ITSELF, read from its own code and settings. "
                "Use it before answering any question about yourself, never "
                "from memory: 'what can you do', 'can you X' when unsure, "
                "'how do I use/turn on X in Sage', 'what's my dip/voice/mic "
                "set to', 'which model are you', 'what version', 'what changed "
                "this week', 'how does your wake word work', 'show me your "
                "code for X'. Parts: overview (version, model), abilities "
                "(tools on/off), settings (server + app settings), how_to "
                "(the user guide), whats_new (recent changes), code (search or "
                "read Sage's source). Read-only: it cannot change anything."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "part": {"type": "string", "enum": list(_PARTS)},
                    "query": {
                        "type": "string",
                        "description": (
                            "What to look for: a feature, setting or code "
                            "term (e.g. 'wake word', 'dip', 'rewind'). "
                            "Optional except for a code search."
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": "part=code: a source file to read.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "part=code with path: first line to read.",
                    },
                    "days": {
                        "type": "integer",
                        "description": "part=whats_new: how far back (default 14).",
                    },
                },
                "required": ["part"],
            },
        )

    def execute(self, **params: Any) -> ToolResult:
        part = str(params.get("part") or "overview").strip().lower()
        query = str(params.get("query") or "")
        try:
            if part == "abilities":
                content = abilities(query)
            elif part == "settings":
                content = settings(query)
            elif part == "how_to":
                content = how_to(query)
            elif part == "whats_new":
                content = whats_new(params.get("days") or 14)
            elif part == "code":
                content = code(
                    query, str(params.get("path") or ""), params.get("start_line") or 1
                )
            else:
                part = "overview"
                content = overview()
        except Exception as exc:  # noqa: BLE001
            logger.warning("about_sage %s failed: %s", part, exc)
            return ToolResult(
                tool_name=self.tool_id,
                content=f"Could not read that about myself: {exc}",
                success=False,
            )
        return ToolResult(
            tool_name=self.tool_id,
            content=content,
            success=True,
            metadata={"part": part},
        )
