"""The voice sidecar is started before the rest of the server's setup.

After a reboot on 29 September the sidecar was launched at the end of
`serve()`'s setup, 29 s after the server started; its imports then took
~40 s, and the first reply sat silent for 17 s waiting for it.
"""

from __future__ import annotations

import ast
from pathlib import Path

SERVE = Path(__file__).resolve().parents[2] / "src" / "openjarvis" / "cli" / "serve.py"


def _calls(node: ast.AST) -> set[str]:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def test_serve_starts_the_voice_sidecar_before_building_the_engine():
    tree = ast.parse(SERVE.read_text(encoding="utf-8"))
    serve = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "serve"
    )
    order = [_calls(statement) for statement in serve.body]
    starts = [i for i, calls in enumerate(order) if "start_if_selected" in calls]
    engine = [i for i, calls in enumerate(order) if "register_builtin_models" in calls]
    assert starts and engine
    assert starts[0] < engine[0]
