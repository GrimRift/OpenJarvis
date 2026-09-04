"""Web search tool — Tavily API with bounded internal routing."""

from __future__ import annotations

import html as _html
import logging
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.security import page_access
from openjarvis.security.ssrf import check_ssrf
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

_NEWS_RE = re.compile(
    r"\b(?:news|headlines?|roundup|trending|breaking|current\s+events?)\b",
    re.IGNORECASE,
)
_OFFICIAL_RE = re.compile(
    r"\b(?:official|announcement|announces?|press\s+release|newsroom|"
    r"launch(?:ed|es)?|release(?:d|s)?(?:\s+date)?)\b",
    re.IGNORECASE,
)
_VERIFY_RE = re.compile(
    r"\b(?:verify|verification|fact[ -]?check|conflicting|confirm|"
    r"true\s+or\s+false|accurate|accuracy)\b",
    re.IGNORECASE,
)
_CURRENT_FIGURE_RE = re.compile(
    r"(?=.*\b(?:current|latest|today|now|recent|this\s+(?:week|month|year))\b)"
    r"(?=.*\b(?:exact|amount|value|number|figure|price|cost|rate|score|"
    r"statistics?|data|how\s+much|increase|decrease|rise|drop|up|down)\b)",
    re.IGNORECASE,
)
_CURRENT_PRODUCT_RE = re.compile(
    r"(?=.*\b(?:current|latest|newest|upcoming)\b)"
    r"(?=.*\b(?:model|version|release|launch|price|availability)\b)",
    re.IGNORECASE,
)
_ORDINARY_OVERVIEW_RE = re.compile(
    r"(?=.*\b(?:game|product)\b)"
    r"(?=.*\b(?:overview|information|gameplay|platforms?|what\s+is|"
    r"official\s+website)\b)",
    re.IGNORECASE,
)
# The tool sees the *model's* rewritten query, not the user's words, and the
# model reliably paraphrases "Find images of X" into "X gameplay images" — live,
# that turned a clear image request into an ordinary search with no gallery. So
# the noun-led forms match too, but only where the noun opens or closes the
# request, which leaves "camera image quality review" an ordinary search.
_EXPLICIT_IMAGE_RE = re.compile(
    r"(?:\b(?:show|find|get|search(?:\s+for)?|look\s+for)\b.{0,45}"
    r"\b(?:images?|pictures?|photos?|screenshots?)\b|"
    r"\b(?:image|photo|picture)\s+search\b|"
    r"^\s*(?:images?|pictures?|photos?|screenshots?)\s+(?:of|for)\b|"
    r"\b(?:images?|pictures?|photos?|screenshots?)\s*$)",
    re.IGNORECASE,
)
_BROAD_WORLD_NEWS_RE = re.compile(
    r"\b(?:world|worldwide|global|international|around\s+the\s+world)\b",
    re.IGNORECASE,
)
_ROUNDUP_RE = re.compile(
    r"\b(?:roundup|headlines?|trending|around|across|all\s+around)\b",
    re.IGNORECASE,
)
_QUERY_NOISE_WORDS = {
    "about",
    "again",
    "all",
    "announcement",
    "announcements",
    "around",
    "called",
    "can",
    "current",
    "day",
    "days",
    "exact",
    "find",
    "for",
    "game",
    "get",
    "headline",
    "headlines",
    "image",
    "images",
    "information",
    "last",
    "latest",
    "look",
    "model",
    "models",
    "month",
    "most",
    "named",
    "new",
    "news",
    "official",
    "ordinary",
    "photo",
    "photos",
    "picture",
    "pictures",
    "please",
    "recent",
    "release",
    "released",
    "releases",
    "roundup",
    "search",
    "show",
    "the",
    "this",
    "today",
    "trending",
    "update",
    "version",
    "week",
    "web",
    "world",
    "year",
    "you",
}
_MONTH_NAMES = {
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
}
_GENERIC_HOST_LABELS = {
    "blog",
    "co",
    "com",
    "gov",
    "info",
    "io",
    "net",
    "news",
    "org",
    "support",
    "www",
}
_OFFICIAL_PAGE_MARKERS = {
    "announcement",
    "blog",
    "news",
    "newsroom",
    "press",
    "product",
    "release",
    "support",
}


@dataclass(frozen=True)
class _SearchPlan:
    depth: str
    topic: str | None
    time_range: str | None
    news: bool
    official: bool
    explicit_images: bool


def _clean_text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return re.sub(r"\s+", " ", _html.unescape(value)).strip()


def _infer_time_range(query: str) -> str | None:
    lowered = query.lower()
    if re.search(r"\b(?:today|yesterday|last\s+24\s+hours?|past\s+day)\b", lowered):
        return "day"
    if re.search(r"\b(?:this|last|past)\s+week\b|\bpast\s+7\s+days?\b", lowered):
        return "week"
    if re.search(r"\b(?:this|last|past)\s+month\b|\bpast\s+30\s+days?\b", lowered):
        return "month"
    if re.search(r"\b(?:this|last|past)\s+year\b", lowered):
        return "year"
    return "day" if _NEWS_RE.search(query) and "latest" in lowered else None


def _build_plan(query: str, *, force_advanced: bool) -> _SearchPlan:
    news = bool(_NEWS_RE.search(query))
    explicit_images = bool(_EXPLICIT_IMAGE_RE.search(query))
    ordinary_overview = bool(_ORDINARY_OVERVIEW_RE.search(query))
    official = bool(_OFFICIAL_RE.search(query)) and not ordinary_overview
    advanced = bool(
        force_advanced
        or news
        or explicit_images
        or _VERIFY_RE.search(query)
        or _CURRENT_FIGURE_RE.search(query)
        or official
        or (_CURRENT_PRODUCT_RE.search(query) and not ordinary_overview)
    )
    return _SearchPlan(
        depth="advanced" if advanced else "basic",
        topic="news" if news else None,
        time_range=_infer_time_range(query) if news else None,
        news=news,
        official=official,
        explicit_images=explicit_images,
    )


def _query_subject_terms(query: str) -> list[str]:
    raw_terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9.+-]*", query)
    terms = list(
        dict.fromkeys(
            term.lower()
            for term in raw_terms
            if len(term) >= 3
            and term.lower() not in _QUERY_NOISE_WORDS
            and term.lower() not in _MONTH_NAMES
            and not re.fullmatch(r"20\d{2}", term)
        )
    )
    named_terms = [
        term.lower()
        for term in raw_terms
        if term.lower() in terms
        and (
            term[0].isupper()
            or term.isupper()
            or any(char.isupper() for char in term[1:])
        )
    ]
    return list(dict.fromkeys(named_terms)) or terms


def _result_searchable_text(result: dict[str, Any]) -> str:
    return " ".join(
        str(result.get(field) or "") for field in ("title", "content", "snippet", "url")
    ).lower()


def _result_year_matches(result: dict[str, Any], query_years: set[str]) -> bool:
    if not query_years:
        return True
    result_years = set(
        re.findall(
            r"\b20\d{2}\b",
            " ".join(
                str(result.get(field) or "")
                for field in ("url", "title", "published_date")
            ),
        )
    )
    return not result_years or not query_years.isdisjoint(result_years)


def _is_direct_news_page(result: dict[str, Any]) -> bool:
    parsed = urlparse(str(result.get("url") or ""))
    path = parsed.path.rstrip("/").lower()
    if not parsed.hostname or not path:
        return False
    if any(marker in path for marker in ("/category/", "/section/", "/tag/", "/feed")):
        return False
    return path not in {
        "/headlines",
        "/international",
        "/latest",
        "/news",
        "/news/world",
        "/world",
        "/world-news",
    }


def _is_search_result(result: dict[str, Any], query: str, *, news: bool) -> bool:
    parsed = urlparse(str(result.get("url") or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if not _clean_text(result.get("title")) and not _clean_text(
        result.get("content") or result.get("snippet")
    ):
        return False
    score = result.get("score")
    if isinstance(score, (int, float)) and score < 0.1:
        return False
    if news and not _is_direct_news_page(result):
        return False
    query_years = set(re.findall(r"\b20\d{2}\b", query))
    if not _result_year_matches(result, query_years):
        return False

    terms = _query_subject_terms(query)
    if news and _BROAD_WORLD_NEWS_RE.search(query):
        terms = []
    if not terms:
        return True
    searchable = _result_searchable_text(result)
    require_all = bool(
        len(terms) <= 3
        and re.search(r"\b(?:called|named|titled)\b", query, re.IGNORECASE)
    )
    return (
        all(term in searchable for term in terms)
        if require_all
        else any(term in searchable for term in terms)
    )


def _filter_relevant_results(
    results: list[dict[str, Any]], query: str, *, news: bool
) -> list[dict[str, Any]]:
    return [
        result
        for result in results
        if isinstance(result, dict) and _is_search_result(result, query, news=news)
    ]


def _host_labels(url: str) -> set[str]:
    hostname = (urlparse(url).hostname or "").lower()
    return {
        label
        for label in re.split(r"[.-]", hostname)
        if len(label) >= 3 and label not in _GENERIC_HOST_LABELS
    }


def _is_official_source(result: dict[str, Any], query: str) -> bool:
    url = str(result.get("url") or "")
    labels = _host_labels(url)
    if not labels:
        return False
    query_terms = set(_query_subject_terms(query))
    if labels.intersection(query_terms):
        return True

    title_terms = set(
        re.findall(r"[a-z0-9]+", _clean_text(result.get("title")).lower())
    )
    path_terms = set(re.findall(r"[a-z0-9]+", urlparse(url).path.lower()))
    has_subject = not query_terms or any(
        term in _result_searchable_text(result) for term in query_terms
    )
    return bool(
        has_subject
        and labels.intersection(title_terms)
        and path_terms.intersection(_OFFICIAL_PAGE_MARKERS)
    )


def _result_domain_count(results: list[dict[str, Any]]) -> int:
    return len(
        {
            (urlparse(str(result.get("url") or "")).hostname or "").lower()
            for result in results
            if result.get("url")
        }
    )


#: Host labels too generic to prove the user meant a particular site.
_GENERIC_HOST_LABELS = frozenset(
    {"www", "com", "net", "org", "news", "blog", "web", "app", "site", "home"}
)


def _query_names_the_only_domain(results: list[dict[str, Any]], query: str) -> bool:
    """Whether every result is from a site the query itself asked for.

    "Search reddit for X" can only ever come back from one domain, so the
    two-domain rule would escalate it every single time -- two provider calls
    for a search that did exactly what was asked. Read off the results rather
    than a list of known sites, so it holds for any site the user names.
    """
    hosts = {
        (urlparse(str(result.get("url") or "")).hostname or "").lower()
        for result in results
        if result.get("url")
    }
    if len(hosts) != 1:
        return False
    lowered = query.lower()
    return any(
        len(label) >= 4 and label not in _GENERIC_HOST_LABELS and label in lowered
        for label in hosts.pop().split(".")
    )


#: What an ordinary search has to come back with before it is accepted.
#:
#: This used to be ``bool(results)`` -- one result from one site counted as
#: sufficient, so the escalation below effectively never fired and a thin
#: answer was returned as though it were a good one. The domain rule is the
#: point of the pair: three results from a single blog is one claim repeated,
#: not corroboration.
MIN_SUFFICIENT_RESULTS = 3
MIN_SUFFICIENT_DOMAINS = 2

#: How wide the one permitted retry goes when evidence came back thin.
#: Above the default rather than at the ceiling: advanced depth already costs
#: more per call, and the two-call bound is deliberately kept.
ESCALATED_MAX_RESULTS = 8


def _read_hint(sources: list[dict[str, Any]], quality_passed: bool) -> str:
    """Point at the page to open when a summary cannot hold the answer.

    Offered only when there is a page worth opening. Silent on a search that
    already answered well, so an ordinary lookup does not grow a suggestion to
    go and browse.
    """
    if not sources:
        return ""
    thin = not quality_passed or all(
        len((source.get("summary") or "").strip()) < 200 for source in sources
    )
    if not thin:
        return ""
    return (
        "If the detail asked for is not in these summaries, it may exist only "
        "on the page itself -- pages often draw schedules, prices and tables "
        "after loading, and no summary will contain those. Use web_read on "
        f"{sources[0]['url']} to read it."
    )


def _results_are_sufficient(
    results: list[dict[str, Any]],
    query: str,
    plan: _SearchPlan,
    *,
    images: list[dict[str, str]],
) -> bool:
    if plan.explicit_images:
        return bool(images or results)
    if plan.official:
        return any(_is_official_source(result, query) for result in results)
    if plan.news and _ROUNDUP_RE.search(query):
        return len(results) >= 3 and _result_domain_count(results) >= 2
    if len(results) < MIN_SUFFICIENT_RESULTS:
        return False
    if _result_domain_count(results) >= MIN_SUFFICIENT_DOMAINS:
        return True
    # One domain is the right answer when the user named that domain.
    return _query_names_the_only_domain(results, query)


def _retry_query(query: str) -> str:
    named_match = re.search(
        r"\b(?:called|named|titled)\s+([A-Za-z0-9][A-Za-z0-9 .+'-]{1,80})",
        query,
        re.IGNORECASE,
    )
    if named_match:
        named_entity = named_match.group(1).strip(" .")
        suffix = (
            " official game overview"
            if re.search(r"\bgame\b", query, re.IGNORECASE)
            else " authoritative source"
        )
        return f'"{named_entity}"{suffix}'
    terms = _query_subject_terms(query)
    if not terms:
        return f"{query} reliable relevant sources"
    exact = " ".join(f'"{term}"' for term in terms[:4])
    if re.search(r"\bgame\b", query, re.IGNORECASE):
        return f"{exact} official game overview"
    return f"{query} {exact} authoritative source"


def _https_image(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("url")
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    return value if parsed.scheme == "https" and parsed.hostname else None


def _source_image(result: dict[str, Any]) -> str | None:
    images = result.get("images")
    if not isinstance(images, list):
        return None
    for image in images:
        if url := _https_image(image):
            return url
    return None


#: How many pictures a gallery aims for. Six fills the grid at both widths --
#: it divides by the 2 columns on a phone and the 3 on a desktop, where five
#: left a ragged hole in the last row.
GALLERY_IMAGE_TARGET = 6

#: Page furniture that is an image but not a picture of anything. Measured on
#: real responses: alongside genuine photographs, result pages carry shop
#: banners, avatars and UI sprites, and one of those in a gallery of six looks
#: worse than five good ones.
_NON_PHOTO_RE = re.compile(
    r"(logo|favicon|sprite|icon|avatar|badge|placeholder|spacer|banner|"
    r"button|pixel|1x1|/ads?/)",
    re.IGNORECASE,
)


def _image_entry(image: Any, seen: set[str]) -> dict[str, str] | None:
    url = _https_image(image)
    if not url or url in seen:
        return None
    seen.add(url)
    description = ""
    if isinstance(image, dict):
        description = _clean_text(image.get("description"))
    return {"url": url, "description": description}


def _gallery_images(
    response: dict[str, Any], target: int = GALLERY_IMAGE_TARGET
) -> list[dict[str, str]]:
    """Pictures for an explicit image search, best first.

    Tavily's own image list is capped at five -- measured at exactly five
    across every query tried, and unaffected by ``max_results``, so asking
    for more results does not yield more pictures. A six-tile gallery cannot
    be filled from it alone.

    The results themselves carry the images found on each page, which are
    plentiful (189 for one query against five curated) but mixed: real
    photographs beside shop banners and interface furniture. They are used
    only to top the curated list up, in result order, and only when they look
    like photographs -- so the curated pictures still come first and a thin
    response degrades to fewer tiles rather than to worse ones.
    """
    output: list[dict[str, str]] = []
    seen: set[str] = set()

    images = response.get("images")
    if isinstance(images, list):
        for image in images:
            if entry := _image_entry(image, seen):
                output.append(entry)

    if len(output) >= target:
        return output

    for result in response.get("results") or []:
        if not isinstance(result, dict):
            continue
        for image in result.get("images") or []:
            if len(output) >= target:
                return output
            entry = _image_entry(image, seen)
            if entry and not _NON_PHOTO_RE.search(entry["url"]):
                output.append(entry)
    return output


def _credits(response: dict[str, Any]) -> int | float:
    value = (response.get("usage") or {}).get("credits") or 0
    return value if isinstance(value, (int, float)) else 0


@ToolRegistry.register("web_search")
class WebSearchTool(BaseTool):
    """Search the web via Tavily with at most one internal escalation."""

    tool_id = "web_search"
    is_local = False

    def __init__(
        self,
        api_key: str | None = None,
        max_results: int = 4,
        *,
        force_advanced: bool = False,
    ):
        self._api_key = api_key or os.environ.get("TAVILY_API_KEY")
        self._max_results = max_results
        self._force_advanced = force_advanced

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="web_search",
            description=(
                "Search the live web. Call this once when the user asks to search, "
                "browse, look up, verify, or requests current information. Pass the "
                "user's request faithfully with the exact entity. Do not add latest, "
                "news, release date, official, or image intent unless the user asked "
                "for it. The tool selects search depth, checks relevance, and performs "
                "at most one corrective provider call."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Complete search query with exact named entities."
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results to return (1-10).",
                        "default": 4,
                    },
                },
                "required": ["query"],
            },
            category="search",
            metadata={"requires_api_key": "TAVILY_API_KEY"},
        )

    @staticmethod
    def _is_url(text: str) -> bool:
        stripped = text.strip()
        return stripped.startswith("http://") or stripped.startswith("https://")

    @staticmethod
    def _extract_url(text: str) -> str | None:
        match = re.search(r"https?://[^\s,;\"'<>]+", text)
        return match.group(0).rstrip(".,;)") if match else None

    @staticmethod
    def _normalize_url(url: str) -> str:
        match = re.match(r"(https?://arxiv\.org)/pdf/(.+?)(?:\.pdf)?$", url)
        if match:
            return f"{match.group(1)}/abs/{match.group(2)}"
        return url

    @staticmethod
    def _fetch_url(url: str, max_chars: int = 6000) -> str:
        import httpx

        url = WebSearchTool._normalize_url(url)
        ssrf_error = check_ssrf(url)
        if ssrf_error:
            raise ValueError(ssrf_error)
        response = httpx.get(
            url.strip(),
            follow_redirects=True,
            timeout=30.0,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; OpenJarvis/1.0; "
                    "+https://github.com/openjarvis)"
                )
            },
        )
        response.raise_for_status()
        if "application/pdf" in response.headers.get("content-type", ""):
            return (
                "[This URL points to a PDF file which cannot be read directly. "
                f"URL: {url}]"
            )
        html = re.sub(
            r"<(script|style)[^>]*>.*?</\1>",
            "",
            response.text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        text = _html.unescape(re.sub(r"<[^>]+>", " ", html))
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > max_chars:
            return text[:max_chars] + "\n\n[Content truncated]"
        return text

    @staticmethod
    def _error(content: str, *, provider_calls: int = 0) -> ToolResult:
        return ToolResult(
            tool_name="web_search",
            content=content,
            success=False,
            metadata={
                "engine": "tavily",
                "provider_calls": provider_calls,
                "bounded_search_complete": True,
            },
        )

    @staticmethod
    def _search_kwargs(
        plan: _SearchPlan, depth: str, max_results: int
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "max_results": max_results,
            "search_depth": depth,
            "include_images": True,
            "include_usage": True,
        }
        if plan.topic:
            kwargs["topic"] = plan.topic
        if plan.time_range:
            kwargs["time_range"] = plan.time_range
        if plan.explicit_images:
            kwargs["include_image_descriptions"] = True
        return kwargs

    def execute(self, **params: Any) -> ToolResult:
        query = str(params.get("query", "") or "").strip()
        if not query:
            return self._error("No query provided.")

        url = self._extract_url(query) if not self._is_url(query) else query
        if url:
            try:
                content = self._fetch_url(url)
                return ToolResult(
                    tool_name="web_search",
                    content=content or "No content found at URL.",
                    success=True,
                    metadata={
                        "url": url,
                        "mode": "fetch",
                        "provider_calls": 0,
                        "bounded_search_complete": True,
                    },
                )
            except Exception as exc:
                return self._error(f"Failed to fetch URL: {exc}")

        try:
            max_results = int(params.get("max_results", self._max_results))
        except (TypeError, ValueError):
            max_results = self._max_results
        max_results = max(1, min(max_results, 10))
        plan = _build_plan(query, force_advanced=self._force_advanced)
        # ``max_results`` is honoured as given. It used to be quietly capped
        # at 3 for a basic search unless the query *text* happened to name a
        # number ("give 8 sources"), so passing max_results=10 returned 3 and
        # nothing said why -- measured before this changed.
        provider_max_results = max_results

        try:
            from tavily import TavilyClient
        except ImportError:
            return self._error(
                "tavily-python not installed. Install with: pip install tavily-python"
            )
        if not self._api_key:
            return self._error("TAVILY_API_KEY is not configured.")

        # The one retry is the chance to do better, so it asks for more than
        # the first attempt did -- and never fewer than the caller wanted.
        retry_max_results = min(max(max_results, ESCALATED_MAX_RESULTS), 10)

        client = TavilyClient(api_key=self._api_key)
        provider_calls = 0
        credits: int | float = 0
        initial_depth = plan.depth
        final_depth = plan.depth
        escalated = False

        try:
            provider_calls += 1
            response = client.search(
                query,
                **self._search_kwargs(plan, plan.depth, provider_max_results),
            )
        except Exception as exc:
            if plan.depth == "advanced":
                logger.debug("Tavily search error: %s", exc)
                return self._error(
                    f"Tavily search error: {exc}", provider_calls=provider_calls
                )
            escalated = True
            final_depth = "advanced"
            try:
                provider_calls += 1
                response = client.search(
                    _retry_query(query),
                    **self._search_kwargs(plan, "advanced", retry_max_results),
                )
            except Exception as retry_exc:
                logger.debug("Tavily search error after escalation: %s", retry_exc)
                return self._error(
                    f"Tavily search error: {retry_exc}",
                    provider_calls=provider_calls,
                )

        credits += _credits(response)
        raw_results = list(response.get("results") or [])
        results = _filter_relevant_results(raw_results, query, news=plan.news)
        images = _gallery_images(response) if plan.explicit_images else []
        quality_passed = _results_are_sufficient(results, query, plan, images=images)

        if initial_depth == "basic" and not escalated and not quality_passed:
            escalated = True
            final_depth = "advanced"
            try:
                provider_calls += 1
                response = client.search(
                    _retry_query(query),
                    **self._search_kwargs(plan, "advanced", retry_max_results),
                )
            except Exception as exc:
                logger.debug("Tavily search error after escalation: %s", exc)
                return self._error(
                    f"Tavily search error: {exc}", provider_calls=provider_calls
                )
            credits += _credits(response)
            results = _filter_relevant_results(
                list(response.get("results") or []), query, news=plan.news
            )
            images = _gallery_images(response) if plan.explicit_images else []
            quality_passed = _results_are_sufficient(
                results, query, plan, images=images
            )

        formatted_parts: list[str] = []
        sources: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for result in results:
            title = _clean_text(result.get("title")) or "Untitled"
            source_url = str(result.get("url") or "")
            if source_url:
                parsed = urlparse(source_url)
                key = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
                if key in seen_urls:
                    continue
                seen_urls.add(key)
            content = _clean_text(result.get("content") or result.get("snippet"))
            published_date = _clean_text(result.get("published_date")) or None
            score = result.get("score")
            if not isinstance(score, (int, float)):
                score = None
            official_source = _is_official_source(result, query)
            model_summary_limit = 600 if final_depth == "basic" else 1000
            model_summary = content[:model_summary_limit]
            sources.append(
                {
                    "title": title,
                    "url": source_url,
                    "summary": content[:500],
                    "image_url": _source_image(result),
                    "published_date": published_date,
                    "score": score,
                    "official_source": official_source,
                }
            )
            published_line = f"\nPublished: {published_date}" if published_date else ""
            official_line = "\nOfficial source: yes" if official_source else ""
            formatted_parts.append(
                f"### {title}\nSource: {source_url}{published_line}{official_line}"
                f"\nSummary: {model_summary}"
            )

        formatted = "\n\n---\n\n".join(formatted_parts)

        # Reading one of these is the same intent as clicking it, so they
        # are permitted for the rest of the turn. A link found later
        # *inside* one of these pages is not, which is the distinction
        # `web_read` enforces.
        page_access.allow(source["url"] for source in sources)
        if not quality_passed:
            warning = (
                "Search results were insufficient or off-topic. State that clearly "
                "and do not invent missing details."
            )
            formatted = f"{warning}\n\n{formatted}" if formatted else warning

        # Said in the result rather than as a prompt rule, because
        # prompt-level rules have not held here. A summary is the search
        # provider's precis; when the answer is a detail the page draws
        # after loading -- showtimes, a price, a table -- no summary will
        # ever contain it, and the model needs telling where it lives.
        hint = _read_hint(sources, quality_passed)
        if hint:
            formatted = f"{formatted}\n\n{hint}" if formatted else hint

        return ToolResult(
            tool_name="web_search",
            content=formatted or "No results found.",
            success=True,
            metadata={
                "num_results": len(sources),
                "engine": "tavily",
                "credits": credits or None,
                "initial_depth": initial_depth,
                "final_depth": final_depth,
                "search_depth": final_depth,
                "topic": plan.topic or "general",
                "time_range": plan.time_range,
                "provider_calls": provider_calls,
                "escalated": escalated,
                "quality_passed": quality_passed,
                "explicit_image_search": plan.explicit_images,
                "images": images,
                "sources": sources,
                "terminal_search": quality_passed,
                "bounded_search_complete": True,
            },
        )


__all__ = ["WebSearchTool"]
