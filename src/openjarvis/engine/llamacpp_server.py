"""Starting Sage's llama.cpp servers: the model router and the embedder.

The router is one ``llama-server`` with no model of its own: it loads the model
a request names from the presets file, and unloads it after
``idle_unload_seconds`` without requests. The embedder is a second, small
``llama-server`` holding nomic-embed-text for memory and retrieval.

Both are started detached rather than tied to this server's lifetime, like the
voice sidecar is: staging and live take turns, and a swap would otherwise
throw away a loaded model only for the next server to load it again. Neither
holds the GPU while idle, so outliving Sage costs nothing.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urlparse

from openjarvis.core.paths import get_data_dir

logger = logging.getLogger(__name__)

#: Prefetching MoE experts overlaps their upload with compute (thecodacus
#: fork); it only acts on models with experts in RAM, here the 35B.
_ROUTER_ENV = {"GGML_SCHED_PREFETCH_EXPERTS": "1"}


def _answers(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _host_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    return parsed.hostname or "127.0.0.1", parsed.port or 8080


def router_args(cfg: Any) -> List[str]:
    host, port = _host_port(cfg.host)
    return [
        cfg.binary_path,
        "--models-preset",
        cfg.models_preset,
        # One chat model at a time: two of them do not fit in 8 GB of VRAM,
        # so picking another one unloads the first.
        "--models-max",
        "1",
        "--sleep-idle-seconds",
        str(int(cfg.idle_unload_seconds)),
        "--host",
        host,
        "--port",
        str(port),
        "--no-webui",
    ]


def embedder_args(cfg: Any) -> List[str]:
    host, port = _host_port(cfg.embedding_host)
    return [
        cfg.binary_path,
        "-m",
        cfg.embedding_model_path,
        "--embedding",
        "--host",
        host,
        "--port",
        str(port),
        # On the CPU: ~150 ms an input, and the chat model needs every MB of
        # the card (with this on the GPU the 9B ran out of VRAM, 2026-10-10).
        "-ngl",
        "0",
        # nomic-embed-text's context; a batch must hold a whole input.
        "-c",
        "2048",
        "-b",
        "2048",
        "-ub",
        "2048",
        "--no-webui",
    ]


def _spawn(args: List[str], log_name: str, extra_env: Optional[dict] = None) -> None:
    log_dir = Path(get_data_dir()) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(extra_env or {})
    creation = 0
    if os.name == "nt":
        creation = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            # Leave the server's job, so closing Sage does not kill it.
            | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        )
    with open(log_dir / log_name, "ab") as log:

        def start(flags: int) -> subprocess.Popen:
            return subprocess.Popen(
                args,
                cwd=str(Path(args[0]).parent),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=flags,
            )

        try:
            proc = start(creation)
        except PermissionError:
            # A job that forbids breakaway refuses the whole start.
            proc = start(
                creation & ~getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
            )
    logger.info("Started %s (pid %s)", Path(args[0]).name, proc.pid)


def load_model(host: str, model: str, timeout: float = 5.0) -> bool:
    """Ask the router to load *model* now, ahead of its first request.

    Returns once loading has started, not when it is done. Best-effort: no
    router, or a model it does not know, is not an error.
    """
    if not model:
        return False
    import json

    req = urllib.request.Request(
        host.rstrip("/") + "/models/load",
        data=json.dumps({"model": model}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        logger.debug("Could not preload %s", model, exc_info=True)
        return False


def embedding_host() -> Optional[str]:
    """The llama.cpp embedding server's URL when one is configured, else None
    (and callers keep talking to Ollama)."""
    try:
        from openjarvis.core.config import load_config

        cfg = load_config().engine.llamacpp
    except Exception:
        return None
    if not cfg.embedding_model_path:
        return None
    return cfg.embedding_host.rstrip("/")


def ensure_servers(cfg: Any, *, wait: float = 15.0) -> bool:
    """Start the router (and embedder) unless they already answer.

    Returns whether the router answers. Never raises: with no local server
    Sage still runs on the cloud model.
    """
    if not (cfg.binary_path and cfg.models_preset):
        return _answers(cfg.host.rstrip("/") + "/health")
    if not Path(cfg.binary_path).is_file():
        logger.warning("llama-server not found at %s", cfg.binary_path)
        return False

    router_up = _answers(cfg.host.rstrip("/") + "/health")
    if not router_up:
        try:
            _spawn(router_args(cfg), "llamacpp-router.log", _ROUTER_ENV)
        except Exception:
            logger.warning("Could not start the llama.cpp router", exc_info=True)
            return False

    if cfg.embedding_model_path and not _answers(
        cfg.embedding_host.rstrip("/") + "/health"
    ):
        try:
            _spawn(embedder_args(cfg), "llamacpp-embed.log")
        except Exception:
            logger.warning("Could not start the embedding server", exc_info=True)

    deadline = time.monotonic() + wait
    while not router_up and time.monotonic() < deadline:
        time.sleep(0.25)
        router_up = _answers(cfg.host.rstrip("/") + "/health")
    if not router_up:
        logger.warning("llama.cpp router did not answer within %.0f s", wait)
    return router_up


__all__ = [
    "embedder_args",
    "embedding_host",
    "ensure_servers",
    "load_model",
    "router_args",
]
