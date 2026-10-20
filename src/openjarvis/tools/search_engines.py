"""The web search engines Sage can use: Tavily, Serper and Exa.

The user's choice (20 October, when Tavily's credits ran out): pick the
engine on the Settings page, and when it fails or is out of credits, fall
over to the next one that has a key and say so.

Every engine answers in Tavily's shape -- ``results`` of title, url,
content, raw_content, published_date, score, images; a top-level ``images``
list; ``usage.credits`` -- so everything ``web_search`` does after the call
(relevance filter, retries, cache, read-ahead, cards) is the same whichever
engine answered. The engine is named in the answer's ``engine`` key.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List
from urllib.parse import urlparse

import httpx

from openjarvis.core.paths import get_config_dir

logger = logging.getLogger(__name__)

#: Also the fall-over order after the chosen engine: with Exa chosen, Tavily
#: (with its backup key) comes next and Serper last -- the user's order.
ENGINES = ("tavily", "serper", "exa")
#: The user's choice after the 20 October live probes: Exa answered 3/3
#: exactly in both runs, Tavily and Serper 2/3 (Tavily once with a wrong
#: score).
DEFAULT_ENGINE = "exa"
LABELS = {"tavily": "Tavily", "serper": "Serper", "exa": "Exa"}
KEY_NAMES = {
    "tavily": "TAVILY_API_KEY",
    "serper": "SERPER_API_KEY",
    "exa": "EXA_API_KEY",
}
#: A second key for the same engine, used when the first is out of credits
#: (the user's Tavily backup, 20 October). The first key stays first.
BACKUP_KEY_NAMES = {"tavily": "TAVILY_API_KEY_BACKUP"}

#: Serper's results follow Google's for this country. The user is in the
#: Philippines and asks for prices in pesos and local places; Tavily had no
#: country set and still found local pages, so this only matches Google's
#: own behaviour for them.
SERPER_COUNTRY = "ph"

#: An engine that answered "no credits" or "bad key" is passed over for this
#: long, so each search does not spend its time asking it again.
SKIP_SECONDS = 30 * 60

_TIME_RANGE_DAYS = {"day": 1, "week": 7, "month": 31, "year": 365}
_SERPER_TBS = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}

_lock = threading.Lock()
_skip_until: Dict[str, float] = {}


# -- the saved choice --------------------------------------------------------


def settings_path() -> Path:
    return get_config_dir() / "search_settings.json"


def load_engine(path: Path | None = None) -> str:
    """The engine chosen in Settings; Exa when nothing (valid) is saved."""
    try:
        raw = json.loads((path or settings_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_ENGINE
    engine = raw.get("engine") if isinstance(raw, dict) else None
    return engine if engine in ENGINES else DEFAULT_ENGINE


def save_engine(engine: str, path: Path | None = None) -> str:
    """Save *engine* as the choice. Raises ValueError for an unknown one."""
    engine = str(engine or "").strip().lower()
    if engine not in ENGINES:
        raise ValueError(f"Unknown search engine {engine!r}")
    target = path or settings_path()
    with _lock:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"engine": engine}, indent=2), encoding="utf-8")
        # A new choice is a fresh start: an engine passed over for credits
        # may have been topped up, which is often why it was chosen again.
        _skip_until.clear()
    return engine


def keys_for(engine: str) -> List[str]:
    """*engine*'s keys in the order to use them: its own, then any backup."""
    names = [KEY_NAMES[engine]]
    if engine in BACKUP_KEY_NAMES:
        names.append(BACKUP_KEY_NAMES[engine])
    keys: List[str] = []
    for name in names:
        value = os.environ.get(name)
        if value and value not in keys:
            keys.append(value)
    return keys


def key_for(engine: str) -> str | None:
    keys = keys_for(engine)
    return keys[0] if keys else None


def status(path: Path | None = None) -> Dict[str, Any]:
    """The choice and each engine's state, for Settings and the Health page."""
    chosen = load_engine(path)
    now = time.monotonic()
    return {
        "engine": chosen,
        "engines": [
            {
                "id": engine,
                "label": LABELS[engine],
                "key_name": KEY_NAMES[engine],
                "key_set": bool(os.environ.get(KEY_NAMES[engine])),
                "backup_key_name": BACKUP_KEY_NAMES.get(engine),
                "backup_key_set": bool(
                    engine in BACKUP_KEY_NAMES
                    and os.environ.get(BACKUP_KEY_NAMES[engine])
                ),
                "passed_over": _skip_until.get(engine, 0.0) > now,
            }
            for engine in ENGINES
        ],
    }


# -- errors ------------------------------------------------------------------


class SearchEngineError(RuntimeError):
    """An engine refused or failed a search (not a timeout)."""

    def __init__(self, engine: str, message: str, *, exhausted: bool = False):
        super().__init__(f"{LABELS[engine]}: {message}")
        self.engine = engine
        #: Out of credits, or the key was refused: worth skipping for a while.
        self.exhausted = exhausted


_EXHAUSTED_WORDS = (
    "credit",
    "usage limit",
    "quota",
    "exceeded",
    "insufficient",
    "unauthorized",
    "invalid api key",
    "forbidden",
)


def _looks_exhausted(exc: BaseException) -> bool:
    if isinstance(exc, SearchEngineError):
        return exc.exhausted
    # tavily-python raises UsageLimitExceededError / InvalidAPIKeyError /
    # ForbiddenError; matched by name so the package stays optional here.
    name = type(exc).__name__.lower()
    if any(word in name for word in ("usagelimit", "invalidapikey", "forbidden")):
        return True
    text = str(exc).lower()
    return any(word in text for word in _EXHAUSTED_WORDS)


def is_timeout(exc: BaseException | None) -> bool:
    # tavily raises its own TimeoutError (not the builtin) on a slow call.
    return isinstance(exc, TimeoutError) or type(exc).__name__ == "TimeoutError"


def _post(
    engine: str, url: str, body: Dict[str, Any], headers: Dict[str, str], timeout: float
) -> Dict[str, Any]:
    try:
        response = httpx.post(url, json=body, headers=headers, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise TimeoutError(f"{LABELS[engine]} timed out") from exc
    except httpx.HTTPError as exc:
        raise SearchEngineError(engine, f"unreachable ({exc})") from exc
    if response.status_code != 200:
        detail = response.text[:200].strip()
        raise SearchEngineError(
            engine,
            f"HTTP {response.status_code} {detail}",
            # 401/403 bad key, 402 out of credits, 429 over the plan's rate.
            exhausted=response.status_code in (401, 402, 403, 429)
            or _looks_exhausted(RuntimeError(detail)),
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise SearchEngineError(engine, "answered with something not JSON") from exc
    return data if isinstance(data, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


# -- Tavily ------------------------------------------------------------------


class TavilyEngine:
    """Tavily, with the backup key used once the first is out of credits.

    A key that answered "out of credits" is passed over for ``SKIP_SECONDS``
    (as a whole engine is), so later searches go straight to the backup.
    Only when every key is used up does the failure reach the chain, which
    then moves on to the next engine.
    """

    name = "tavily"

    def __init__(self, api_key: str, *more_keys: str):
        from tavily import TavilyClient  # ImportError if not installed

        keys = [api_key, *more_keys, *keys_for("tavily")]
        unique = [k for i, k in enumerate(keys) if k and k not in keys[:i]]
        self._clients = [
            (f"tavily#{index}", TavilyClient(api_key=key))
            for index, key in enumerate(unique)
        ]

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        now = time.monotonic()
        live = [c for c in self._clients if _skip_until.get(c[0], 0.0) <= now]
        last: BaseException | None = None
        for slot, client in live or self._clients:
            try:
                result = getattr(client, method)(*args, **kwargs)
            except Exception as exc:
                if is_timeout(exc) or not _looks_exhausted(exc):
                    raise
                last = exc
                with _lock:
                    _skip_until[slot] = time.monotonic() + SKIP_SECONDS
                logger.info("Tavily key %s is used up (%s)", slot, exc)
                continue
            if slot != "tavily#0":
                logger.info("Tavily answered with backup key %s", slot)
            return result
        assert last is not None
        raise last

    def search(self, query: str, **kwargs: Any) -> Dict[str, Any]:
        return dict(self._call("search", query, **kwargs) or {})

    def extract(self, url: str, timeout: float) -> List[Dict[str, Any]]:
        response = self._call(
            "extract", urls=[url], extract_depth="basic", format="text", timeout=timeout
        )
        return [
            {"url": r.get("url"), "title": r.get("title"), "text": r.get("raw_content")}
            for r in response.get("results") or []
        ]


# -- Serper (Google results) -------------------------------------------------


class SerperEngine:
    """Google's results through serper.dev, one credit a call.

    Google gives a short snippet per result, not page text: an answer box or
    knowledge panel, when Google shows one, leads the results because it is
    usually the exact figure asked for. Page text comes from web_read's
    read-ahead, as it does for any thin summary.
    """

    name = "serper"
    BASE = "https://google.serper.dev"

    def __init__(self, api_key: str):
        self._headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

    def _call(self, path: str, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        return _post(self.name, f"{self.BASE}/{path}", body, self._headers, timeout)

    def search(
        self,
        query: str,
        *,
        timeout: float | None = None,
        max_results: int = 5,
        topic: str | None = None,
        time_range: str | None = None,
        include_images: bool = False,
        include_image_descriptions: bool = False,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        timeout = timeout or 8.0
        news = topic == "news"
        body: Dict[str, Any] = {
            "q": query,
            "num": max(1, min(int(max_results), 10)),
            "gl": SERPER_COUNTRY,
        }
        if time_range in _SERPER_TBS:
            body["tbs"] = _SERPER_TBS[time_range]

        # An explicit picture request (Tavily's descriptions flag) also asks
        # Google Images, alongside: a gallery needs real pictures, and the
        # web results carry at most a thumbnail each.
        pool: ThreadPoolExecutor | None = None
        images_future = None
        if include_images and include_image_descriptions:
            pool = ThreadPoolExecutor(max_workers=1)
            images_future = pool.submit(
                self._call, "images", {"q": query, "gl": SERPER_COUNTRY}, timeout
            )
        try:
            data = self._call("news" if news else "search", body, timeout)
            image_data: Dict[str, Any] = {}
            if images_future is not None:
                try:
                    image_data = images_future.result(timeout=timeout)
                except Exception as exc:  # noqa: BLE001
                    logger.info("Serper image search failed (%s)", exc)
        finally:
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)

        results = self.map_results(data, news=news)
        credits = data.get("credits") or 0
        images = []
        for item in (image_data.get("images") or [])[:10]:
            url = _text(item.get("imageUrl"))
            if url:
                images.append({"url": url, "description": _text(item.get("title"))})
        if image_data:
            credits += image_data.get("credits") or 0
        return {
            "engine": self.name,
            "query": query,
            "results": results,
            "images": images,
            "usage": {"credits": credits},
        }

    @staticmethod
    def map_results(data: Dict[str, Any], *, news: bool) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if not news:
            box = data.get("answerBox") or {}
            answer = " ".join(
                part
                for part in (
                    _text(box.get("answer")),
                    _text(box.get("snippet")),
                )
                if part
            )
            if answer and _text(box.get("link")):
                results.append(
                    {
                        "title": _text(box.get("title")) or "Google answer",
                        "url": _text(box.get("link")),
                        "content": answer,
                    }
                )
            graph = data.get("knowledgeGraph") or {}
            graph_url = _text(graph.get("descriptionLink")) or _text(
                graph.get("website")
            )
            if graph_url and (graph.get("description") or graph.get("attributes")):
                facts = "; ".join(
                    f"{key}: {value}"
                    for key, value in (graph.get("attributes") or {}).items()
                )
                results.append(
                    {
                        "title": _text(graph.get("title")) or "Knowledge panel",
                        "url": graph_url,
                        "content": " ".join(
                            p for p in (_text(graph.get("description")), facts) if p
                        ),
                        "images": [graph["imageUrl"]] if graph.get("imageUrl") else [],
                    }
                )
        for item in data.get("news" if news else "organic") or []:
            url = _text(item.get("link"))
            if not url:
                continue
            content = _text(item.get("snippet"))
            if news and item.get("source"):
                content = f"{content} ({_text(item.get('source'))})".strip()
            results.append(
                {
                    "title": _text(item.get("title")),
                    "url": url,
                    "content": content,
                    "published_date": _text(item.get("date")) or None,
                    "images": [item["imageUrl"]] if item.get("imageUrl") else [],
                }
            )
        return results


# -- Exa (neural index, page text included) ----------------------------------


class ExaEngine:
    """Exa's own index; page text for the results comes with the search."""

    name = "exa"
    BASE = "https://api.exa.ai"

    def __init__(self, api_key: str):
        self._headers = {"x-api-key": api_key, "Content-Type": "application/json"}

    def search(
        self,
        query: str,
        *,
        timeout: float | None = None,
        max_results: int = 5,
        topic: str | None = None,
        time_range: str | None = None,
        include_raw_content: Any = False,
        **_ignored: Any,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "query": query,
            "numResults": max(1, min(int(max_results), 10)),
            "type": "auto",
            "contents": {
                # A whole page only when an exact figure is asked for (the
                # excerpt is cut from it); otherwise enough for a summary.
                "text": {"maxCharacters": 8000 if include_raw_content else 1500},
            },
        }
        if topic == "news":
            body["category"] = "news"
        if time_range in _TIME_RANGE_DAYS:
            start = datetime.now(timezone.utc) - timedelta(
                days=_TIME_RANGE_DAYS[time_range]
            )
            body["startPublishedDate"] = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        data = _post(
            self.name, f"{self.BASE}/search", body, self._headers, timeout or 8.0
        )
        return {
            "engine": self.name,
            "query": query,
            "results": self.map_results(data),
            "images": [],
            "usage": {"credits": 0},
            "cost_usd": (data.get("costDollars") or {}).get("total"),
        }

    @staticmethod
    def map_results(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        results = []
        for item in data.get("results") or []:
            url = _text(item.get("url"))
            if not url:
                continue
            # Exa lists its own library pages (exa.ai/library/...) among
            # the results; they are never what was asked about.
            host = (urlparse(url).hostname or "").lower()
            if host == "exa.ai" or host.endswith(".exa.ai"):
                continue
            text = _text(item.get("text"))
            highlights = " ".join(
                _text(h) for h in item.get("highlights") or [] if _text(h)
            )
            results.append(
                {
                    "title": _text(item.get("title")),
                    "url": url,
                    "content": highlights or text[:1000],
                    "raw_content": text or None,
                    "published_date": _text(item.get("publishedDate")) or None,
                    "score": item.get("score"),
                    "images": [item["image"]] if item.get("image") else [],
                }
            )
        return results

    def extract(self, url: str, timeout: float) -> List[Dict[str, Any]]:
        data = _post(
            self.name,
            f"{self.BASE}/contents",
            {"urls": [url], "text": True},
            self._headers,
            timeout,
        )
        return [
            {"url": r.get("url"), "title": r.get("title"), "text": r.get("text")}
            for r in data.get("results") or []
        ]


_FACTORIES: Dict[str, Callable[[str], Any]] = {
    "tavily": TavilyEngine,
    "serper": SerperEngine,
    "exa": ExaEngine,
}


# -- the chain ---------------------------------------------------------------


def order(chosen: str | None = None) -> List[str]:
    """The chosen engine first, then the others, each only with a key."""
    first = chosen if chosen in ENGINES else load_engine()
    ranked = [first] + [engine for engine in ENGINES if engine != first]
    return [engine for engine in ranked if key_for(engine)]


class EngineChain:
    """``search`` on the chosen engine, falling over to the next on failure.

    A timeout is not fallen over: the search's time is spent, and
    ``web_search`` already answers a timeout with its own plain retry. Any
    other failure -- no credits, a refused key, a dead connection -- goes to
    the next engine that has a key, and the answer records which engine it
    came from and what happened to the ones before it.
    """

    def __init__(self, engines: List[Any], chosen: str):
        self.engines = engines
        self.chosen = chosen

    @property
    def names(self) -> List[str]:
        return [engine.name for engine in self.engines]

    def search(self, query: str, **kwargs: Any) -> Dict[str, Any]:
        passed: List[str] = []
        last: BaseException | None = None
        now = time.monotonic()
        live = [e for e in self.engines if _skip_until.get(e.name, 0.0) <= now]
        for engine in self.engines:
            if engine not in live:
                passed.append(f"{LABELS[engine.name]} (out of credits or key refused)")
        # Every engine passed over: try them anyway rather than not at all.
        for engine in live or self.engines:
            try:
                response = engine.search(query, **kwargs)
            except Exception as exc:
                if is_timeout(exc):
                    raise
                last = exc
                exhausted = _looks_exhausted(exc)
                if exhausted:
                    with _lock:
                        _skip_until[engine.name] = time.monotonic() + SKIP_SECONDS
                logger.info(
                    "%s search failed (%s); trying the next engine", engine.name, exc
                )
                reason = "out of credits or key refused" if exhausted else "failed"
                passed.append(f"{LABELS[engine.name]} ({reason})")
                continue
            response["engine"] = engine.name
            # Only a chosen engine that was tried and failed is worth saying:
            # one with no key at all would add the note to every answer, and
            # Settings already shows the missing key.
            if engine.name != self.chosen and passed:
                response["passed_over"] = passed
            return response
        assert last is not None
        raise last


def chain(tavily_key: str | None = None) -> EngineChain | None:
    """The engines to search with, chosen first; None when no key is set.

    *tavily_key* overrides the environment's (the tool's constructor takes
    one). Tavily is left out when its package is not installed.
    """
    chosen = load_engine()
    names = order(chosen)
    if tavily_key and "tavily" not in names:
        names.append("tavily")
    engines = []
    for name in names:
        key = tavily_key if (name == "tavily" and tavily_key) else key_for(name)
        try:
            assert key
            engines.append(_FACTORIES[name](key))
        except ImportError:
            logger.info("tavily-python is not installed; Tavily left out")
    return EngineChain(engines, chosen) if engines else None


def extractor() -> Any | None:
    """An engine that can read a page for web_read, chosen engine first.

    Serper has no page reader, so with Serper chosen the next engine with a
    key and a reader is used, and none means web_read goes on to the browser.
    """
    now = time.monotonic()
    for name in order():
        if name == "serper" or _skip_until.get(name, 0.0) > now:
            continue
        try:
            return _FACTORIES[name](key_for(name) or "")
        except ImportError:
            continue
    return None


def note_failure(engine: str, exc: BaseException) -> bool:
    """Pass *engine* over for a while if *exc* says it is out of credits.

    True when it was passed over (worth trying the next engine).
    """
    if not _looks_exhausted(exc):
        return False
    with _lock:
        _skip_until[engine] = time.monotonic() + SKIP_SECONDS
    return True


def ping(engine: str, key: str, timeout: float) -> int:
    """One minimal search; the HTTP status (for the Health page)."""
    if engine == "tavily":
        url, body, headers = (
            "https://api.tavily.com/search",
            {"api_key": key, "query": "ping", "max_results": 1},
            {},
        )
    elif engine == "serper":
        url, body, headers = (
            f"{SerperEngine.BASE}/search",
            {"q": "ping", "num": 1},
            {"X-API-KEY": key},
        )
    else:
        url, body, headers = (
            f"{ExaEngine.BASE}/search",
            {"query": "ping", "numResults": 1},
            {"x-api-key": key},
        )
    return httpx.post(url, json=body, headers=headers, timeout=timeout).status_code


def reset_skips() -> None:
    """Forget passed-over engines (tests)."""
    with _lock:
        _skip_until.clear()
