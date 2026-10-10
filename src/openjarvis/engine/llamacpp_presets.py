"""Reading and changing the llama.cpp router's model presets from Settings.

Only a whitelisted set of keys is editable, each validated against a range,
so the Settings page cannot write an argument that breaks the router. Edits
keep the file's comments and order: a key is replaced in place, or added at
the end of its section.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: key -> (kind, allowed). kind "int" takes (min, max); "choice" a tuple.
EDITABLE: Dict[str, Tuple[str, Any]] = {
    "ctx-size": ("int", (2048, 32768)),
    "cache-type-k": ("choice", ("f16", "q8_0", "q4_0")),
    "cache-type-v": ("choice", ("f16", "q8_0", "q4_0")),
    "n-gpu-layers": ("int", (0, 99)),
    "n-cpu-moe": ("int", (0, 99)),
}
#: Keys of the shared ``[*]`` section.
GLOBAL_EDITABLE: Dict[str, Tuple[str, Any]] = {
    "sleep-idle-seconds": ("int", (30, 3600)),
}
GLOBAL = "*"


def _split(text: str) -> List[Tuple[Optional[str], List[str]]]:
    """``[(section or None for the preamble, lines)]`` in file order."""
    sections: List[Tuple[Optional[str], List[str]]] = [(None, [])]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            sections.append((stripped[1:-1], [line]))
        else:
            sections[-1][1].append(line)
    return sections


def _key_of(line: str) -> Optional[str]:
    stripped = line.strip()
    if not stripped or stripped.startswith((";", "#")) or "=" not in stripped:
        return None
    return stripped.split("=", 1)[0].strip()


def read_presets(path: Path) -> Dict[str, Dict[str, str]]:
    """Every section's ``key = value`` pairs (comments dropped)."""
    out: Dict[str, Dict[str, str]] = {}
    for name, lines in _split(path.read_text(encoding="utf-8")):
        if name is None:
            continue
        values: Dict[str, str] = {}
        for line in lines[1:]:
            key = _key_of(line)
            if key:
                values[key] = line.split("=", 1)[1].strip()
        out[name] = values
    return out


def _validate(key: str, value: Any, table: Dict[str, Tuple[str, Any]]) -> str:
    if key not in table:
        raise ValueError(f"{key} cannot be changed here")
    kind, allowed = table[key]
    if kind == "int":
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a whole number") from None
        low, high = allowed
        if not low <= number <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
        return str(number)
    text = str(value)
    if text not in allowed:
        raise ValueError(f"{key} must be one of {', '.join(allowed)}")
    return text


def update_presets(path: Path, changes: Dict[str, Dict[str, Any]]) -> None:
    """Apply ``{section: {key: value}}``, validating everything first.

    Raises ``ValueError`` (and writes nothing) on an unknown section, a key
    that is not editable, or a value out of range.
    """
    text = path.read_text(encoding="utf-8")
    sections = _split(text)
    names = {name for name, _ in sections if name is not None}
    clean: Dict[str, Dict[str, str]] = {}
    for section, values in changes.items():
        if section not in names:
            raise ValueError(f"no model named {section}")
        table = GLOBAL_EDITABLE if section == GLOBAL else EDITABLE
        clean[section] = {k: _validate(k, v, table) for k, v in values.items()}

    out: List[str] = []
    for name, lines in sections:
        pending = dict(clean.get(name or "", {}))
        body = list(lines)
        for i, line in enumerate(body):
            key = _key_of(line)
            if key in pending:
                body[i] = f"{key} = {pending.pop(key)}"
        # New keys go after the section's last setting, before trailing blanks.
        insert_at = len(body)
        while insert_at > 1 and not body[insert_at - 1].strip():
            insert_at -= 1
        for key, value in pending.items():
            body.insert(insert_at, f"{key} = {value}")
            insert_at += 1
        out.extend(body)
    # The router fails to parse a file with a byte-order mark.
    path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")


def reload_router(host: str) -> bool:
    """Ask the router to re-read the presets; a loaded model whose settings
    changed is unloaded and picks them up on its next request."""
    try:
        url = host.rstrip("/") + "/models?reload=1"
        with urllib.request.urlopen(url, timeout=5) as resp:
            json.load(resp)
            return True
    except Exception:
        logger.debug("Router reload failed", exc_info=True)
        return False


__all__ = [
    "EDITABLE",
    "GLOBAL",
    "GLOBAL_EDITABLE",
    "read_presets",
    "reload_router",
    "update_presets",
]
