"""Invariants for the traps that cost time on 15-16 September 2026.

Each of these bit at least twice before it was named. They are pinned
structurally so the next session cannot re-learn them by ear.
"""

from __future__ import annotations

import ast
import re
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
    outputs: what is spoken, the chime, the initiative writer -- and, since
    reminders, the toast-and-voice delivery.
    """

    REQUIRED = frozenset({"speaker", "chimer", "initiative_composer", "reminder"})

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


class TestWakeWordVerificationOnlyEverRemovesFirings:
    """The transcript check on a wake-word firing can reject a firing but
    must never be the reason the wake word goes deaf. Every failure path in
    ``verify`` -- no backend, no audio, a timeout, any exception -- returns a
    confirmed verdict; the socket only sends ``rejected`` when a transcript
    was actually read and did not contain the phrase.
    """

    MODULE = SRC / "speech" / "wake_word_verify.py"

    def test_every_return_in_verify_before_the_transcript_confirms(self):
        tree = _parse(self.MODULE)
        verify = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "verify"
        )
        early: list[tuple[int, str]] = []
        for node in ast.walk(verify):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            call = node.value
            if not (isinstance(call, ast.Call) and _call_name(call) == "Verdict"):
                continue
            first = call.args[0] if call.args else None
            if isinstance(first, ast.Constant):
                early.append((node.lineno, repr(first.value)))
        assert early, "expected literal Verdict(...) returns on the failure paths"
        assert all(value == "True" for _, value in early), (
            f"a failure path in verify() returns a rejection: {early}"
        )

    def test_the_route_rejects_only_on_a_read_transcript(self):
        source = (SRC / "server" / "api_routes.py").read_text(encoding="utf-8")
        assert "if verdict is not None and not verdict.confirmed:" in source


class TestTestsNeverLoadTheSmallVerifierModel:
    """`local_verifier_backend` builds a real Whisper on first use. A test that
    reaches it downloads a model and runs the GPU, which is how a route test
    silently became a forty-second one. Every test module that exercises the
    verifier or the wake-word socket must stub it.
    """

    def test_every_test_touching_the_verifier_stubs_the_model(self):
        offenders = []
        for path in TESTS.rglob("test_*.py"):
            text = path.read_text(encoding="utf-8")
            uses = (
                "from openjarvis.speech.wake_word_verify import" in text
                or 'websocket_connect("/v1/speech/wake-word' in text
            )
            if uses and "local_verifier_backend" not in text:
                offenders.append(path.relative_to(ROOT).as_posix())
        assert offenders == [], (
            "these tests can reach the real verifier model; monkeypatch "
            f"openjarvis.speech.wake_word_verify.local_verifier_backend: {offenders}"
        )


class TestEveryRegisteredSpeechBackendImports:
    """`speech/deepgram.py` imported a class the installed SDK no longer had
    and raised on every call, for as long as the SDK had been at v7; nothing
    noticed because the import was inside a try. A backend the package lists
    must import cleanly and register.
    """

    def test_listed_modules_import_and_register(self):
        import importlib

        from openjarvis.core.registry import SpeechRegistry
        from openjarvis.speech import _discovery

        init = (SRC / "speech" / "__init__.py").read_text(encoding="utf-8")
        listed = re.findall(r'"([a-z_]+)"', init.split("for _mod in")[1].split(")")[0])
        assert listed, "no backend modules listed in speech/__init__.py"
        for mod in listed:
            module = importlib.import_module(f"openjarvis.speech.{mod}")
            backend = next(
                v
                for v in vars(module).values()
                if isinstance(v, type)
                and getattr(v, "backend_id", "")
                and v.__module__ == module.__name__
            )
            # Other test modules clear the registry mid-run; re-register the
            # way the module's own decorator did, so the check is about the
            # module, not about test order.
            if not SpeechRegistry.contains(backend.backend_id):
                SpeechRegistry.register_value(backend.backend_id, backend)
        for key in _discovery.DISCOVERY_ORDER:
            assert SpeechRegistry.contains(key), f"{key} listed but not registered"
