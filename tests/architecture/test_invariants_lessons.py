"""Invariants for the traps that cost time on 15-16 September 2026.

Each of these bit at least twice before it was named. They are pinned
structurally so the next session cannot re-learn them by ear.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "openjarvis"
TESTS = ROOT / "tests"


def _parse(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:  # pragma: no cover - fails loudly
        pytest.fail(f"{path} does not parse: {exc}")


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


class TestRigsNeverReachTheSpeakersOrTheCloud:
    """A test run once played the real chime through the speakers thirty
    times, because the rig faked the voice but not the chime. Every
    construction of the moment engine in a test must fake all three
    outputs: what is spoken, the chime, and the initiative writer.
    """

    REQUIRED = frozenset({"speaker", "chimer", "initiative_composer"})

    def _constructions(self) -> list[tuple[str, int, set[str]]]:
        found = []
        for path in sorted(TESTS.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for node in ast.walk(_parse(path)):
                if isinstance(node, ast.Call) and _call_name(node) == "MomentEngine":
                    keywords = {kw.arg for kw in node.keywords if kw.arg}
                    found.append(
                        (path.relative_to(ROOT).as_posix(), node.lineno, keywords)
                    )
        return found

    def test_there_is_something_to_guard(self):
        assert self._constructions(), "no test builds a MomentEngine any more"

    def test_every_engine_in_a_test_is_silent(self):
        offenders = [
            f"{path}:{line} missing {sorted(self.REQUIRED - keywords)}"
            for path, line, keywords in self._constructions()
            if not self.REQUIRED <= keywords
        ]
        assert not offenders, (
            f"these test rigs would reach the real speakers or the cloud: {offenders}"
        )


class TestReasoningModelsGetRoomToWrite:
    """gpt-5.6-luna thinks before it writes and the thinking counts against
    max_tokens. With 120 tokens the initiative writer came back empty every
    time and read as a decline; with 4,000 the hygiene pass over 400 facts
    did the same and read as nothing to clean. Any direct generate() call in
    the modules that pin the cloud model must leave real headroom.
    """

    MODULES = (
        SRC / "core" / "moments.py",
        SRC / "memory" / "hygiene.py",
        SRC / "memory" / "extractor.py",
        SRC / "speech" / "speculative.py",
    )
    MINIMUM = 600

    def _literal_budgets(self) -> list[tuple[str, int, int]]:
        found = []
        for path in self.MODULES:
            if not path.exists():
                continue
            for node in ast.walk(_parse(path)):
                if not (isinstance(node, ast.Call) and _call_name(node) == "generate"):
                    continue
                for kw in node.keywords:
                    if kw.arg == "max_tokens" and isinstance(kw.value, ast.Constant):
                        if isinstance(kw.value.value, int):
                            found.append(
                                (
                                    path.relative_to(ROOT).as_posix(),
                                    node.lineno,
                                    kw.value.value,
                                )
                            )
        return found

    def test_there_is_something_to_guard(self):
        assert self._literal_budgets(), "no literal max_tokens on a generate() call"

    def test_no_direct_cloud_call_is_starved(self):
        starved = [
            f"{path}:{line} max_tokens={budget}"
            for path, line, budget in self._literal_budgets()
            if budget < self.MINIMUM
        ]
        assert not starved, (
            f"these leave a reasoning model no room to answer: {starved}. "
            f"Use at least {self.MINIMUM}; an empty reply is silently read as "
            "'nothing to say'."
        )

    def test_the_extractor_default_has_headroom(self):
        tree = _parse(SRC / "memory" / "extractor.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                for arg, default in zip(
                    node.args.kwonlyargs, node.args.kw_defaults, strict=True
                ):
                    if arg.arg == "max_tokens":
                        assert isinstance(default, ast.Constant)
                        assert default.value >= self.MINIMUM, (
                            "FactExtractor's default max_tokens is too small for "
                            "the cloud model it now runs on"
                        )
                        return
        pytest.fail("FactExtractor.__init__ has no max_tokens default")
