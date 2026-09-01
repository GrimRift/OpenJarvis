"""Structural invariants: assertions about how the code is wired.

These do not test behaviour. Each one says "there is no code path by which X
can happen", which is the thing a behaviour test structurally cannot say --- it
can only cover the scenarios someone thought to write.

Every invariant here exists because the same failure shape has now cost this
project several debugging sessions: **the code was correct, it just was not the
code being run.**

- Streamed TTS landed in ``sendMessage``; Flux Ultra replies take
  ``releaseSpeculativeAnswer``, a second send path with its own batch-TTS
  block. 38 tests passed, none touching it.
- Markdown was flattened for speech in one of the three places that call TTS,
  so tables were read aloud as "vertical bar".
- ``generate_speculative`` defaulted to ``temperature=0.3`` while every other
  reply used the configured 0.7, so Ultra answers repeated the same joke.
- Reading the class schedule wrote the notify state, burning the day's
  reminders on a check.
- 2026-09-01: retiring a spent search tool cleared the *whole* per-request tool
  list. 247 backend and 182 frontend tests passed, because none exercised a
  turn holding two tools.

Write these against the AST, never against the source text. A grep-shaped
assertion fails on a comment, a reformat, or a changed quote style --- a
false alarm that teaches people to delete the test. Comments in this repo
mention the very identifiers some of these forbid.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "openjarvis"


def _modules() -> list[tuple[Path, ast.Module]]:
    """Every shipped module, parsed once."""
    parsed = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            parsed.append((path, ast.parse(path.read_text(encoding="utf-8"))))
        except SyntaxError as exc:  # pragma: no cover - a broken tree fails loudly
            pytest.fail(f"{path} does not parse: {exc}")
    return parsed


def _calls(tree: ast.Module) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _call_name(node: ast.Call) -> str:
    """The called name, whether ``f()`` or ``obj.f()``."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _rel(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


class TestSpeculationTemperature:
    """Speculative answers must not silently use a different temperature.

    ``generate_speculative`` carries its own ``temperature=0.3`` default while
    ordinary replies use the configured 0.7. The route never overrode it, so
    Ultra mode answered near-deterministically and told the same joke verbatim
    every time. The default is not the bug --- relying on it is.
    """

    TARGET = "generate_speculative"

    def _invocations(self) -> list[tuple[str, ast.Call]]:
        """Calls that run the target, however they reach it.

        It is dispatched as ``asyncio.to_thread(generate_speculative, ...)``,
        which forwards keywords, so a direct-call-only check passes vacuously.
        Both shapes are collected, and the canary below is what caught that.
        """
        found = []
        for path, tree in _modules():
            for call in _calls(tree):
                direct = _call_name(call) == self.TARGET
                dispatched = any(
                    isinstance(arg, ast.Name) and arg.id == self.TARGET
                    for arg in call.args
                )
                if direct or dispatched:
                    found.append((f"{_rel(path)}:{call.lineno}", call))
        return found

    def test_no_call_omits_an_explicit_temperature(self):
        offenders = [
            where
            for where, call in self._invocations()
            if not any(kw.arg == "temperature" for kw in call.keywords)
        ]
        assert not offenders, (
            f"{self.TARGET} invoked without an explicit temperature at "
            f"{offenders}. Pass the configured value; the 0.3 default is not it."
        )

    def test_the_invariant_has_something_to_guard(self):
        """A rename would otherwise make the check above vacuously pass."""
        assert self._invocations(), (
            f"no {self.TARGET} call sites found — did it move or get renamed?"
        )


class TestSpokenTextIsUnavoidable:
    """Every path that reaches a TTS backend must flatten markdown first.

    Three places call TTS and only one flattened, so a table read aloud as
    "vertical bar". A backend reached from a module that never calls
    ``to_spoken_text`` is a fourth path with the same defect.
    """

    #: Reaching one of these means audio is about to be produced.
    BACKEND_CALLS = frozenset({"astream_pcm", "synthesize"})

    #: The TTS backends themselves define ``synthesize``; they are the thing
    #: being guarded, not an entry point into it.
    EXEMPT_PREFIXES = ("speech/",)

    def _entry_points(self) -> dict[str, set[int]]:
        found: dict[str, set[int]] = {}
        for path, tree in _modules():
            rel = _rel(path)
            if rel.startswith(self.EXEMPT_PREFIXES):
                continue
            lines = {
                call.lineno
                for call in _calls(tree)
                if _call_name(call) in self.BACKEND_CALLS
            }
            if lines:
                found[rel] = lines
        return found

    def test_every_tts_entry_point_flattens_first(self):
        flatteners = {
            _rel(path)
            for path, tree in _modules()
            if any(_call_name(c) == "to_spoken_text" for c in _calls(tree))
        }
        offenders = {
            module: sorted(lines)
            for module, lines in self._entry_points().items()
            if module not in flatteners
        }
        assert not offenders, (
            f"these reach a TTS backend without calling to_spoken_text: {offenders}. "
            "Markdown sent straight to TTS is read aloud as punctuation."
        )

    def test_the_invariant_has_something_to_guard(self):
        assert self._entry_points(), "no TTS entry points found — did they move?"


class TestOnlyTheNotifierWritesNotifyState:
    """A read must not consume the day's reminders.

    ``_drop_already_notified`` recorded state while *checking*, so the 10-minute
    poll marked the 11am class notified and the reminder never fired. The state
    is now written only after a delivery succeeds, and only by the notifier.
    """

    STATE_STEM = "class_schedule_notify_state"

    def _modules_mentioning_state(self) -> set[str]:
        found = set()
        for path, tree in _modules():
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if self.STATE_STEM in node.value:
                        found.add(_rel(path))
        return found

    def test_exactly_one_module_owns_the_state_file(self):
        owners = self._modules_mentioning_state()
        assert owners == {"tools/notify_class_schedule.py"}, (
            f"class-schedule notify state is referenced by {owners}. "
            "Only the notifier may name it; a second writer re-creates the bug "
            "where checking the schedule consumed the reminder."
        )


class TestGoogleCallsRefreshTheirToken:
    """A Google access token lives an hour; every call must be able to refresh.

    ``sync()`` always wrapped its reads in ``call_with_refresh``. The write
    methods did not — they read the stored token directly — so accepting an
    invite, declining one, or creating an event failed with a bare 401 for most
    of any given day. Found live: a read through ``sync()`` worked, and
    creating an event minutes later returned 401.

    Calling a ``_gcal_api_*`` helper directly is the shape of that bug. The
    helpers take a token as their first argument, so a direct call necessarily
    supplies one from somewhere that cannot refresh it.
    """

    MODULE = "connectors/gcalendar.py"

    def _direct_api_calls(self) -> list[str]:
        offenders = []
        for path, tree in _modules():
            if _rel(path) != self.MODULE:
                continue
            for call in _calls(tree):
                if (
                    isinstance(call.func, ast.Name)
                    and call.func.id.startswith("_gcal_api_")
                ):
                    offenders.append(f"{call.func.id}:{call.lineno}")
        return offenders

    def test_no_helper_is_called_outside_call_with_refresh(self):
        assert not self._direct_api_calls(), (
            f"called directly instead of via call_with_refresh: "
            f"{self._direct_api_calls()}. These fail with 401 once the "
            "hour-long access token expires."
        )

    def test_the_module_is_still_where_this_expects(self):
        """A move would make the check above vacuously pass."""
        assert any(_rel(path) == self.MODULE for path, _ in _modules()), (
            f"{self.MODULE} not found — did it move?"
        )


class TestToolResultsAreLabelledInOnePlace:
    """Injection labelling must sit on the path every tool result takes.

    ``ToolExecutor.execute`` is that path. Scanning per-tool instead would mean
    the next tool added --- M32's screen reader, say --- silently skips it, and
    on-screen text is the whole reason the labelling exists.
    """

    MODULE = "tools/_stubs.py"
    TARGET = "_label_injection"

    def _call_sites(self) -> list[str]:
        sites = []
        for path, tree in _modules():
            for call in _calls(tree):
                if _call_name(call) == self.TARGET:
                    sites.append(f"{_rel(path)}:{call.lineno}")
        return sites

    def test_it_is_invoked_from_exactly_one_place(self):
        sites = self._call_sites()
        assert len(sites) == 1, (
            f"{self.TARGET} is called from {sites}. A second call site means a "
            "second execution path, and the one that forgets it is the bug."
        )
        assert sites[0].startswith(self.MODULE), sites

    def test_the_invariant_has_something_to_guard(self):
        """A rename or deletion would otherwise pass silently."""
        assert self._call_sites(), (
            f"no {self.TARGET} call site — tool results are no longer scanned."
        )


class TestEveryOpenAISerializerHandlesImages:
    """There is more than one place that builds an OpenAI message payload.

    ``engine/_base.messages_to_dicts`` serves engine-backed models;
    ``server/cloud_router._to_openai_msgs`` serves cloud models, which bypass
    the engine entirely. Teaching only the first about vision left the second
    dropping the image silently, and the model replied "I can't see an image
    attached" — no error, nothing in a log.

    A third serializer added later would fail the same way, so the rule is:
    anything that emits OpenAI ``image_url`` parts must be reachable from this
    list, and anything on this list must actually read ``images``.
    """

    SERIALIZERS = {
        "engine/_base.py": "messages_to_dicts",
        "server/cloud_router.py": "_to_openai_msgs",
    }

    def _function(self, module: str, name: str) -> ast.FunctionDef | None:
        for path, tree in _modules():
            if _rel(path) != module:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == name:
                    return node
        return None

    def test_each_known_serializer_reads_images(self):
        missing = []
        for module, name in self.SERIALIZERS.items():
            func = self._function(module, name)
            if func is None:
                missing.append(f"{module}::{name} (not found)")
                continue
            source = ast.dump(func)
            if "'images'" not in source and '"images"' not in source:
                missing.append(f"{module}::{name} (never reads images)")
        assert not missing, (
            f"these build OpenAI payloads without handling images: {missing}. "
            "An image reaching one of them is dropped silently."
        )

    def test_no_unlisted_module_emits_image_parts(self):
        """A new serializer must be added to SERIALIZERS, not written quietly.

        Matched on the *shape* ``{"type": "image_url", ...}``, not on the bare
        string: ``tools/web_search.py`` carries an ``image_url`` field of
        Tavily's own, which is an unrelated thing wearing the same name. A
        looser check flagged it and would have been deleted as noise.
        """
        emitters = set()
        for path, tree in _modules():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                for key, value in zip(node.keys, node.values):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "type"
                        and isinstance(value, ast.Constant)
                        and value.value == "image_url"
                    ):
                        emitters.add(_rel(path))
        unlisted = emitters - set(self.SERIALIZERS)
        assert not unlisted, (
            f"{unlisted} emit OpenAI image parts but are not listed here. Add "
            "them, so the next vision change updates every payload builder."
        )
