"""Letting a scheduled agent run on a model other than the server's default.

A cron run reaches its agent through ``JarvisSystem.ask()``, which takes no
model argument, and ``jarvis scheduler run-task`` does not pass one either. So
without this an unattended agent is stuck on whatever the server started with
-- ``qwen3.5:4b`` here -- no matter what its own config asks for.

Extracted from ``ProactiveAgent``, which needed it first so that the tier
deciding what auto-executes is judged on a strong model. The digest needs the
same lever for a different reason: on the full evidence set (Gmail, Outlook,
Teams and a required deadline preamble) the small local model overruns the
200-word budget by 30% and degrades or drops a whole source, while the cloud
model covers every source inside it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)


def apply_configured_model(
    args: tuple,
    kwargs: Dict[str, Any],
    model: str,
    engine_key: str,
    *,
    label: str = "agent",
) -> Tuple[tuple, Dict[str, Any]]:
    """Swap in a configured model/engine before ``BaseAgent`` stores them.

    Callers pass ``(engine, model)`` positionally (the orchestrator) or by
    keyword (tests, scripts), so both are handled. Naming only a model is
    usually enough: the server's engine is normally a ``MultiEngine``, which
    routes by model-name prefix and sends a ``gpt-*`` model to the cloud engine
    on its own. ``engine`` is the escape hatch for a single-backend setup,
    where swapping the model alone would ask the local runtime for a model it
    does not have.
    """
    if not model and not engine_key:
        return args, kwargs

    args = list(args)
    if model:
        if len(args) > 1:
            args[1] = model
        elif "model" in kwargs:
            kwargs["model"] = model

    if engine_key:
        try:
            from openjarvis.core.config import load_config
            from openjarvis.engine._discovery import get_engine

            resolved = get_engine(
                load_config(), engine_key=engine_key, model=model or None
            )
            if resolved is not None and resolved[0] == engine_key:
                if args:
                    args[0] = resolved[1]
                elif "engine" in kwargs:
                    kwargs["engine"] = resolved[1]
            else:
                logger.warning(
                    "%s engine %r unavailable for model %r; keeping the default engine",
                    label,
                    engine_key,
                    model,
                )
        except Exception:
            logger.warning(
                "Failed to resolve %s engine %r", label, engine_key, exc_info=True
            )

    return tuple(args), kwargs


__all__ = ["apply_configured_model"]
