"""Keep scheduler tests off the machine: no preference file, no Ollama."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_model_preference(monkeypatch):
    """A task with no model asks the user's preference and, run locally,
    unloads the model from Ollama. Neither may reach the real machine."""
    from openjarvis.core import model_preference

    unloaded: list = []
    monkeypatch.setattr(model_preference, "background_model", lambda *a, **k: None)
    monkeypatch.setattr(
        model_preference,
        "unload_local_model",
        lambda model, *a, **k: unloaded.append(model),
    )
    return unloaded
