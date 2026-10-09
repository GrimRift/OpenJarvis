"""Agentic research loop over the hybrid-search tool.

A small, self-contained planner-executor loop:

* the planner is supplied by the caller (the web endpoint resolves it from
  config, falling back to ``gemma4:31b`` on Ollama for legacy installs),
* it always has :meth:`HybridSearch.search` over the local personal
  knowledge corpus, and optionally live web search (Tavily) when a
  ``WebSearchTool`` is passed in,
* it gets up to ``max_iterations`` tool calls,
* tool results are trimmed before re-entering the context window, and
* the final reply must cite specific hits.

The loop is deliberately decoupled from the rest of the agent scaffolding
(`ToolUsingAgent`, `EventBus`, `AgentContext`, etc.) so the surface stays
small. Anything that wants tracing or registry integration can wrap it.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from openjarvis.connectors.hybrid_search import HybridSearch, SearchHit
from openjarvis.core.types import Message, Role, ToolCall
from openjarvis.engine._base import InferenceEngine
from openjarvis.tools.web_search import WebSearchTool

logger = logging.getLogger(__name__)


DEFAULT_PLANNER_MODEL = "gemma4:31b"


CLARIFY_TOOL_SPEC: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "clarify",
        "description": (
            "Ask the user a clarifying question and wait for their answer. "
            "Only use AFTER at least one search has been attempted. Use when "
            "search results are ambiguous (e.g. three different people share "
            "a first name), search returned zero results and the query likely "
            "needs reframing, or the scope is too broad to synthesize "
            "meaningfully. Never use clarify before searching."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "The clarifying question to ask the user. Be specific "
                        "about what you need to know to make progress."
                    ),
                },
            },
            "required": ["question"],
        },
    },
}


SEARCH_TOOL_SPEC: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search",
        "description": (
            "Hybrid search over the user's personal knowledge corpus (emails, "
            "notes, calendar events, attachments). Combines BM25 lexical match "
            "with dense embedding similarity, ranked by reciprocal rank fusion. "
            "Use structured filters (person, time_range, sources) whenever the "
            "user names a specific person or time window. Each call returns up "
            "to 'limit' results with content snippets and thread context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Natural-language query. Use the topic the user is asking "
                        "about. Can be empty when filtering purely by person or "
                        "time (e.g. 'list all mail from Kelly in May')."
                    ),
                },
                "person": {
                    "type": "string",
                    "description": (
                        "Filter to messages involving this person. Matches a "
                        "substring of the name or email address — 'Kelly' or "
                        "'@tldrnewsletter.com' both work."
                    ),
                },
                "time_range": {
                    "type": "object",
                    "description": "ISO 8601 datetime range. Either bound may be omitted.",
                    "properties": {
                        "start": {"type": "string", "description": "ISO 8601 start"},
                        "end": {"type": "string", "description": "ISO 8601 end"},
                    },
                },
                "sources": {
                    "type": "array",
                    "description": (
                        "Restrict the search to one or more connectors. Use this "
                        'whenever the user names a data source (e.g. "in my '
                        'Granola notes" → [\'granola\']; "check Slack and Gmail" '
                        "→ ['slack', 'gmail']). Valid IDs include: gmail, slack, "
                        "granola, notion, obsidian, gcalendar, gdrive, gmail_imap, "
                        "outlook, imessage, whatsapp, apple_notes, apple_contacts, "
                        "gcontacts, google_tasks, github_notifications."
                    ),
                    "items": {"type": "string"},
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 20, cap 20).",
                    "default": 20,
                },
            },
            "required": ["query"],
        },
    },
}


WEB_SEARCH_TOOL_SPEC: Dict[str, Any] = WebSearchTool(
    force_advanced=True
).to_openai_function()

# Appended into SYSTEM_PROMPT only when a WebSearchTool was actually passed to
# ResearchAgent — otherwise the model isn't told about a tool it can't call.
_WEB_TOOLS_SECTION = "\n    web_search(query, max_results=5)"
_WEB_READ_SECTION = "\n    web_read(urls=[...])"
# Deep Research used to get exactly one web_search and no way to open a page
# (9 October: "best gaming laptops under 60,000 pesos" ended with "share a
# few listings and I'll compare them"). It now researches the way the name
# says: several searches on different angles, then the best pages read.
_WEB_TOOLS_STRATEGY = (
    "\n  10. Anything public -- products, prices, news, how-to, comparisons,"
    " laws, places, current events -- is NOT in the personal corpus: start"
    " with web_search and do not spend calls on `search` for it. Use `search`"
    " only for the user's own emails, notes, calendar and documents."
    "\n  11. Research properly: up to {max_web_searches} web_search calls, each"
    " on a DIFFERENT angle of the question (for an EV purchase: running"
    " costs, charging access, incentives, local prices). Put the exact entity,"
    " locale (the user is in the Philippines unless they say otherwise), date"
    " window and the value wanted into each query. Several web_search calls"
    " may go in one response; they run at the same time."
    "{web_read_strategy}"
    "\n  12. Never answer a public question without at least one web_search."
    " If the user corrects an entity name, use the corrected name. Distinguish"
    " projections from confirmed outcomes and state exact dates when freshness"
    " matters. web_search results are not part of the personal corpus"
    " — cite their URLs directly in your answer text, not as [N] brackets."
)
_WEB_READ_STRATEGY = (
    " When a summary does not hold the detail (a price list, specs, a"
    " table), open the most useful pages with web_read -- pass up to 3 in"
    " `urls` in ONE call; {max_reads} pages in all. Only pages a search"
    " returned can be read."
)
_WEB_TOOLS_SYNTHESIS = (
    "\n  - For web_search results, cite the source URL directly in the"
    " answer text instead — do not invent a [N] number for a web result."
)


SYSTEM_PROMPT = """You are a research assistant with access to the user's personal knowledge corpus.

The user's corpus contains data from these sources only:
{available_sources}

You answer questions by calling these tools:

    search(query, person=None, time_range=None, sources=None, limit=20)
    clarify(question){web_tools_section}{web_read_section}

Strategy:
  1. If the user names a specific OTHER person (a real name, or "from Kelly", "with @company.com"), ALWAYS pass `person=` rather than relying on lexical match — hybrid search will fuzzy-match name or address fragments. Do NOT pass `person=` for generic first-person references like "my", "I", or "me" (e.g. "my class schedule", "what do I have today") — plenty of records (notes, reference documents, schedules) have no author/participant metadata at all, so filtering by the user's own identity on those returns zero results even when the content exists. For anything about the user's own notes/documents/schedule, search by content/topic only, with no `person` filter.
  2. When the user mentions ANY time window — "this past week", "recently", "last month", "past few days", "yesterday" — you MUST translate it to a `time_range` parameter. Today is {today} ({today_weekday}). This only applies to time-bound content (emails, messages, calendar events) where the stored timestamp is when it happened. Reference/informational content (notes, schedules, documents) is stored with an ingestion timestamp, not an "as-of" date — a weekly class schedule note, for example, does not stop matching "today" or "this week" just because it was saved on a different day. Do NOT pass `time_range` when searching for that kind of evergreen reference content; search by content/topic only and let the content's own text (e.g. day names, dates written inside it) answer the time-relative question.
  3. The `time_range` argument is a JSON object: `{{"start": "<ISO 8601>", "end": "<ISO 8601>"}}`. Either bound may be omitted, but pass at least one whenever the user gave you a temporal cue.
  4. When the user names a specific data source — "my Granola notes", "in Slack", "from my email" — you MUST pass `sources=[...]` with the matching connector ID. Only use IDs that appear in the connected-sources list above; do NOT invent or assume sources that are not connected. Common synonyms: "meeting notes"/"meetings"/"transcripts" → granola; "email"/"inbox" → gmail; "DMs"/"channels" → slack. Without this filter the search returns mail/messages ABOUT a tool instead of records FROM that tool.
  4a. Never apologize about sources that aren't in the connected-sources list — if the user asks about "Notion" but Notion isn't connected, just say "Notion isn't connected, but here's what I found in {available_sources}" and answer from what is available.
  5. When the user asks for "next", "upcoming", "future", or "soon" calendar events/meetings/appointments, use `sources=["gcalendar"]` if gcalendar is connected, set `time_range={{"start": "{today}"}}`, and use `query=""` unless the user gave a specific topic such as "dentist" or "music lesson". This returns the nearest upcoming calendar items across calendars instead of keyword-matching only birthdays or event titles.
  6. If the first structured search returns nothing useful, broaden with a semantic query and drop filters one at a time.
  7. You have a clarify tool. Only use it AFTER at least one search attempt. Use it when: you found multiple ambiguous matches (e.g. 3 different people named John), search returned zero results and the query might need reframing, or the scope is too broad to synthesize meaningfully. Never use clarify before searching — always try first.
  8. After receiving a clarify response, use the information to construct a precise search with the correct person, time_range, sources, and query parameters. Only use an empty query when structured filters carry the request; never send a search with no concrete parameters. Extract every concrete signal from the user's reply (names, dates, topics, sources) and put it on the call.
  9. Tool calls — across all available tools — share a budget of {tool_budget} total. Spend wisely.{web_tools_strategy}

Synthesis rules:
  - For personal-corpus `search` hits, cite sources as individual numbers in square brackets. Always separate — write [4] [7] [20], never [4, 7, 20]. Never format citations as markdown links. Just the number in brackets: [1]. The `ref` field on each hit is the citation number.{web_tools_synthesis}
  - Quote sender / date / subject when relevant — the user wants attribution.
  - If the search returned nothing relevant, say so plainly. Do not invent results.
  - Only state facts that appear in the retrieved search results. Never supplement with your own knowledge or training data. If you are unsure whether a fact came from the search results, do not include it.

Today's date is {today} ({today_weekday}). Trust this stated weekday over any
date arithmetic you might otherwise do yourself — do not recompute or guess
which day of the week {today} falls on.
"""


# ---------------------------------------------------------------------------
# Tool-result shaping
# ---------------------------------------------------------------------------


def _trim_thread_context(ctx: List[Dict[str, Any]], cap: int) -> List[Dict[str, Any]]:
    """Keep the first ``cap`` entries; mark elision when trimming."""
    if len(ctx) <= cap:
        return ctx
    trimmed = list(ctx[:cap])
    trimmed.append({"snippet": f"… {len(ctx) - cap} more chunks in thread …"})
    return trimmed


def shape_results_for_model(
    hits: List[SearchHit],
    *,
    detailed_top: int = 5,
    thread_ctx_per_hit: int = 3,
    total_cap: int = 20,
    ref_offset: int = 0,
) -> Dict[str, Any]:
    """Compact a hit list into a JSON payload the planner can chew through.

    The first ``detailed_top`` rows keep their content snippet and trimmed
    thread context; the remainder are summarised to title + sender + date so
    the planner still sees the breadth of what's available without blowing the
    context window. Each hit gets a numeric ``ref`` (1-indexed, plus
    ``ref_offset``) so the synthesis can cite it as ``[N]``. The offset lets
    multi-search runs hand the planner globally unique refs across calls so
    a later renumbering pass can dedupe by first appearance.
    """
    out_hits: List[Dict[str, Any]] = []
    visible = hits[:total_cap]
    for i, h in enumerate(visible):
        sender = h.participants[0] if h.participants else ""
        base = {
            "ref": i + 1 + ref_offset,
            "title": h.title,
            "sender": sender,
            "timestamp": h.timestamp,
            "source": h.source,
            "score": round(h.score, 4),
        }
        if i < detailed_top:
            base["snippet"] = h.content_snippet
            if h.thread_context:
                base["thread"] = _trim_thread_context(
                    h.thread_context, thread_ctx_per_hit
                )
        out_hits.append(base)
    return {
        "num_results": len(hits),
        "shown": len(visible),
        "truncated": len(hits) > total_cap,
        "hits": out_hits,
    }


def _hit_date(timestamp: str) -> str:
    """Pull a ``YYYY-MM-DD`` date out of a SearchHit timestamp (best effort)."""
    if not timestamp:
        return ""
    try:
        return (
            datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date().isoformat()
        )
    except (ValueError, AttributeError):
        return str(timestamp)[:10]


def _bare_doc_id(source: str, document_id: str) -> str:
    """Strip the connector prefix from a stored ``doc_id``.

    Gmail ingest writes ``doc_id="gmail:<hex_message_id>"`` so that ids stay
    unique across connectors. The Gmail web UI only resolves the bare hex
    message id; passing the full prefixed form 404s and bounces the user
    back to the inbox. Other connectors can be added here as we link out
    to them.
    """
    if not document_id:
        return ""
    prefix = f"{source}:"
    if source and document_id.startswith(prefix):
        return document_id[len(prefix) :]
    return document_id


def _hit_url(source: str, document_id: str) -> str:
    """Reconstruct a clickable URL from a hit's ``doc_id`` alone.

    Used as a *fallback* when the connector didn't persist a URL on the
    chunk (``SearchHit.url`` is empty). Reconstruction only works for
    sources whose doc_id encodes everything the permalink needs — Gmail
    and Slack today. Sources whose doc_id is just an opaque ID (e.g.
    Granola, where the web URL uses a different UUID than the API note_id)
    must populate ``Document.url`` at ingest time; we can't make a working
    link from the doc_id alone.

    Gmail ids land here in two flavors:

    - **Hex message id** (``19dfa2ccbeff78b0``) — what the OAuth Gmail
      connector stores. Resolves directly via ``#all/<id>`` permalink.
    - **RFC822 Message-ID** (``<CABCD@mail.gmail.com>``) — what the IMAP
      connector stores, since IMAP doesn't expose Gmail's internal hex
      id. The permalink form would 404; instead route through Gmail's
      search URL with the ``rfc822msgid:`` operator, which lands the user
      on the specific message.

    Slack doc_ids encode workspace + channel + timestamp as
    ``slack:{team_domain}:{channel_id}:{ts}`` so the permalink
    ``https://{team_domain}.slack.com/archives/{channel_id}/p{ts}`` can
    be reconstructed without a side lookup. Legacy two-segment ids
    (``slack:{channel_id}:{ts}`` from earlier ingests) fall back to the
    workspace-less ``slack.com/archives/...`` form.
    """
    if source == "gmail" and document_id:
        msg_id = _bare_doc_id(source, document_id)
        if not msg_id:
            return ""
        if "@" in msg_id or "<" in msg_id or ">" in msg_id:
            rfc_id = msg_id.strip("<>")
            return f"https://mail.google.com/mail/u/0/#search/rfc822msgid:{rfc_id}"
        return f"https://mail.google.com/mail/u/0/#all/{msg_id}"
    if source == "slack" and document_id:
        bare = _bare_doc_id(source, document_id)
        if not bare:
            return ""
        parts = bare.split(":")
        if len(parts) >= 3:
            team_domain, channel_id, ts = parts[0], parts[1], ":".join(parts[2:])
        elif len(parts) == 2:
            team_domain = ""
            channel_id, ts = parts
        else:
            return ""
        if not channel_id or not ts:
            return ""
        ts_clean = ts.replace(".", "")
        if team_domain:
            return f"https://{team_domain}.slack.com/archives/{channel_id}/p{ts_clean}"
        return f"https://slack.com/archives/{channel_id}/p{ts_clean}"
    return ""


_CITE_RE = re.compile(r"\[(\d+)\]")


def renumber_citations(
    text: str,
    ref_to_source: Dict[int, Dict[str, Any]],
) -> Tuple[str, List[Dict[str, Any]]]:
    """Renumber ``[N]`` citations in ``text`` by first-appearance order.

    The planner sees globally-offset refs across multiple search calls
    (search 1 returns 1..20, search 2 returns 21..40, …). When the
    synthesis arrives, the first ref the model actually cited becomes
    ``[1]``, the second unique one becomes ``[2]``, and so on. Repeats
    map to the same new ref. Refs the synthesis never cites are dropped
    from the returned ``sources`` list — only the ones the user can
    actually click on get carried through.

    Parameters
    ----------
    text:
        Synthesis text containing inline ``[N]`` references.
    ref_to_source:
        Mapping from the original (offset) ref to the source dict that
        ``build_sources_for_client`` produced for that hit.

    Returns
    -------
    (new_text, ordered_sources)
        ``new_text`` has every cited ``[N]`` rewritten to its new
        sequence number. ``ordered_sources`` is the deduped list of
        source dicts in the order they appear in the synthesis, each
        with its ``ref`` field set to the new sequence number.
    """
    old_to_new: Dict[int, int] = {}
    ordered: List[Dict[str, Any]] = []
    for m in _CITE_RE.finditer(text):
        try:
            old = int(m.group(1))
        except ValueError:
            continue
        if old in old_to_new:
            continue
        src = ref_to_source.get(old)
        if src is None:
            # Synthesis cited a ref that doesn't exist in the corpus —
            # leave the literal text alone, drop the source entry.
            continue
        new_ref = len(ordered) + 1
        old_to_new[old] = new_ref
        renumbered_src = dict(src)
        renumbered_src["ref"] = new_ref
        ordered.append(renumbered_src)

    def _replace(match: "re.Match[str]") -> str:
        try:
            old = int(match.group(1))
        except ValueError:
            return match.group(0)
        new = old_to_new.get(old)
        return f"[{new}]" if new is not None else match.group(0)

    new_text = _CITE_RE.sub(_replace, text)
    return new_text, ordered


def build_sources_for_client(
    hits: List[SearchHit],
    *,
    total_cap: int = 20,
    ref_offset: int = 0,
) -> List[Dict[str, Any]]:
    """Produce the citation-friendly sources list streamed to the frontend.

    One entry per hit, in the same order the planner sees them — so a
    ``[N]`` citation in the synthesis maps to ``sources[N - 1]`` on the
    client. We don't deduplicate by ``document_id``: separate chunks of the
    same email each get their own citation slot since the planner may quote
    different parts.
    """
    out: List[Dict[str, Any]] = []
    for i, h in enumerate(hits[:total_cap]):
        sender = h.participants[0] if h.participants else ""
        # Prefer the URL the connector stored at ingest time (Granola's
        # ``web_url``, Notion's page URL, etc.) — it's the only reliable
        # link for sources whose web URL doesn't derive from the doc_id.
        # Fall back to the doc_id-based reconstruction for sources where
        # that still works (Slack, Gmail).
        url = h.url or _hit_url(h.source, h.document_id)
        out.append(
            {
                "ref": i + 1 + ref_offset,
                "title": h.title,
                "sender": sender,
                "date": _hit_date(h.timestamp),
                "source": h.source,
                "source_id": _bare_doc_id(h.source, h.document_id),
                "url": url,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


@dataclass
class ToolInvocation:
    """One tool call together with what the planner asked for and got.

    ``tool_name`` is ``"search"``, ``"clarify"``, or ``"web_search"``. For
    search calls, ``num_results``, ``top_titles`` and ``raw_hits`` are
    populated; for clarify calls, ``response`` holds the user's answer; for
    web_search calls, ``response`` holds the tool's formatted output text.
    """

    arguments: Dict[str, Any]
    num_results: int = 0
    top_titles: List[str] = field(default_factory=list)
    raw_hits: List[SearchHit] = field(default_factory=list)
    sources: List[Dict[str, Any]] = field(default_factory=list)
    images: List[Dict[str, str]] = field(default_factory=list)
    explicit_image_search: bool = False
    tool_name: str = "search"
    response: str = ""
    success: bool = True


def _default_clarify_handler(question: str) -> str:
    """Prompt the user on stdout and read a one-line answer from stdin.

    Empty answers are echoed back as a sentinel so the planner doesn't think
    the user was silent because of an upstream error.
    """
    print(file=sys.stderr)
    print(f"\033[1m🤔 Clarification needed:\033[0m {question}", file=sys.stderr)
    try:
        answer = input("> ").strip()
    except EOFError:
        return "(no answer provided)"
    return answer or "(user did not provide a clarification)"


def _reads_spent(web_read: Any) -> bool:
    from openjarvis.security import page_access

    return page_access.reads_used() >= int(getattr(web_read, "_max_reads", 3))


class _StreamFellBack(Exception):
    """Streaming failed before any output; use the plain call instead."""


@dataclass
class ResearchResult:
    answer: str
    iterations: int
    tool_calls: List[ToolInvocation]
    usage: Dict[str, int] = field(default_factory=dict)


class ResearchAgent:
    """Planner + executor loop over a single hybrid-search tool.

    Parameters
    ----------
    engine:
        An ``InferenceEngine`` that supports OpenAI-style ``tools`` in
        ``generate`` (Ollama with a tool-capable model).
    search:
        The HybridSearch instance the planner can call.
    web_search:
        Optional WebSearchTool instance. When ``None`` (default), the
        planner isn't offered a web_search tool at all — e.g. no
        TAVILY_API_KEY configured. When provided, the planner can search
        the live web alongside the personal-corpus search.
    model:
        Planner model tag (default ``gemma4:31b``).
    max_iterations:
        Hard ceiling on tool calls before the loop is forced into synthesis.
    temperature, max_tokens, num_ctx:
        Generation parameters passed through to ``engine.generate``.
    on_event:
        Optional callback fired at loop milestones so callers (e.g. the SSE
        research router) can stream progress without rewriting the loop.
        Receives a dict in one of these shapes:
          - ``{"type": "search_call", "arguments": {...}}`` — about to call search
          - ``{"type": "search_result", "num_hits": N, "top_titles": [...], "sources": [{"ref": 1, "title": ..., "sender": ..., "date": ..., "source_id": ..., "url": ...}, ...]}`` — search returned
          - ``{"type": "clarify_call", "question": "..."}`` — about to ask for clarification
          - ``{"type": "clarify_response", "response": "..."}`` — clarification received
          - ``{"type": "final_answer", "text": "..."}`` — synthesis ready
        The callback runs on the same thread as ``run`` and must be non-blocking.
    """

    def __init__(
        self,
        engine: InferenceEngine,
        search: HybridSearch,
        *,
        web_search: Optional[WebSearchTool] = None,
        model: str = DEFAULT_PLANNER_MODEL,
        max_iterations: int = 5,
        temperature: float = 0.3,
        max_tokens: int = 1500,
        num_ctx: int = 16384,
        clarify_handler: Optional[Callable[[str], str]] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
        available_sources: Optional[List[str]] = None,
        web_read: Optional[Any] = None,
        max_web_searches: int = 1,
        time_budget_seconds: Optional[float] = None,
        stream: bool = False,
    ) -> None:
        self._engine = engine
        self._search = search
        self._web_search = web_search
        #: A WebReadTool, when pages may be opened.
        self._web_read = web_read
        self._max_web_searches = max(1, int(max_web_searches))
        #: Past this, the next round gets no tools and writes the answer.
        self._time_budget = time_budget_seconds
        #: Stream each round through ``engine.stream_full`` and emit the text
        #: as ``synthesis`` events while it is written.
        self._stream = bool(stream)
        self._model = model
        self._max_iterations = int(max_iterations)
        self._temperature = float(temperature)
        self._max_tokens = int(max_tokens)
        self._num_ctx = int(num_ctx)
        self._clarify_handler = clarify_handler or _default_clarify_handler
        self._on_event = on_event
        # Explicit list wins; otherwise we'll discover sources from the
        # KnowledgeStore on each run() call so the prompt stays accurate
        # even as the user connects new connectors mid-session.
        self._available_sources_override = available_sources

    def _emit(self, event: Dict[str, Any]) -> None:
        """Fire ``self._on_event`` if set; swallow callback errors."""
        if self._on_event is None:
            return
        try:
            self._on_event(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("on_event callback raised %s — ignoring", exc)

    # ------------------------------------------------------------------
    # Argument parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_time_range(raw: Any):
        if not raw or not isinstance(raw, dict):
            return None

        def _maybe(v):
            if not v:
                return None
            try:
                return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            except ValueError:
                return None

        start = _maybe(raw.get("start"))
        end = _maybe(raw.get("end"))
        if start is None and end is None:
            return None
        return (start, end)

    def _execute_search(self, args: Dict[str, Any]) -> ToolInvocation:
        query = str(args.get("query", "") or "")
        person = args.get("person") or None
        time_range = self._parse_time_range(args.get("time_range"))
        sources = args.get("sources") or None
        if sources and not isinstance(sources, list):
            sources = [str(sources)]
        limit = int(args.get("limit", 20) or 20)
        limit = max(1, min(limit, 20))

        hits = self._search.search(
            query,
            person=person,
            time_range=time_range,
            sources=sources,
            limit=limit,
        )
        titles = [h.title or (h.content_snippet[:60] + "…") for h in hits[:5]]
        return ToolInvocation(
            tool_name="search",
            arguments={
                "query": query,
                "person": person,
                "time_range": (
                    {
                        "start": time_range[0].isoformat()
                        if time_range and time_range[0]
                        else None,
                        "end": time_range[1].isoformat()
                        if time_range and time_range[1]
                        else None,
                    }
                    if time_range
                    else None
                ),
                "sources": sources,
                "limit": limit,
            },
            num_results=len(hits),
            top_titles=titles,
            raw_hits=hits,
        )

    def _execute_web_search(self, args: Dict[str, Any]) -> ToolInvocation:
        query = str(args.get("query", "") or "")
        max_results = int(args.get("max_results", 5) or 5)
        web_args: Dict[str, Any] = {"query": query, "max_results": max_results}
        result = self._web_search.execute(**web_args)
        metadata = result.metadata or {}
        return ToolInvocation(
            tool_name="web_search",
            arguments=web_args,
            num_results=int(metadata.get("num_results", 0)),
            sources=list(metadata.get("sources") or []),
            images=list(metadata.get("images") or []),
            explicit_image_search=bool(metadata.get("explicit_image_search")),
            response=result.content,
            success=result.success,
        )

    def _execute_web_read(self, args: Dict[str, Any]) -> ToolInvocation:
        urls = args.get("urls") or []
        if isinstance(urls, str):
            urls = [urls]
        read_args: Dict[str, Any] = {"urls": [str(u) for u in urls][:3]}
        if args.get("url"):
            read_args["url"] = str(args["url"])
        result = self._web_read.execute(**read_args)
        metadata = result.metadata or {}
        return ToolInvocation(
            tool_name="web_read",
            arguments=read_args,
            num_results=int(metadata.get("pages_read", 1 if result.success else 0)),
            sources=list(metadata.get("sources") or []),
            response=result.content,
            success=result.success,
        )

    # ------------------------------------------------------------------
    # Model calls
    # ------------------------------------------------------------------

    def _generate(
        self, messages: List[Message], tools: Optional[List[Dict[str, Any]]]
    ) -> Dict[str, Any]:
        """One model round: streamed when asked and possible, else plain."""
        if self._stream and hasattr(self._engine, "stream_full"):
            try:
                return self._generate_streamed(messages, tools)
            except _StreamFellBack:
                pass
        return self._engine.generate(
            messages,
            model=self._model,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            num_ctx=self._num_ctx,
            tools=tools,
        )

    def _generate_streamed(
        self, messages: List[Message], tools: Optional[List[Dict[str, Any]]]
    ) -> Dict[str, Any]:
        """Like ``engine.generate`` but the text goes out as it is written.

        The answer was written in full and only then sent, so a Deep
        Research turn showed nothing for its whole length (63 s on 9
        October). Text written in a round that then calls tools was a
        preamble, not the answer: it is taken back with ``synthesis_replace``.
        """
        import asyncio

        content: List[str] = []
        fragments: Dict[int, Dict[str, Any]] = {}
        usage: Dict[str, Any] = {}

        async def pump() -> None:
            kwargs: Dict[str, Any] = {}
            if tools:
                kwargs["tools"] = tools
            async for chunk in self._engine.stream_full(
                messages,
                model=self._model,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                **kwargs,
            ):
                if chunk.content:
                    content.append(chunk.content)
                    self._emit({"type": "synthesis", "text": chunk.content})
                for fragment in chunk.tool_calls or []:
                    index = int(fragment.get("index", 0))
                    entry = fragments.setdefault(
                        index, {"id": "", "name": "", "arguments": ""}
                    )
                    if fragment.get("id"):
                        entry["id"] = fragment["id"]
                    function = fragment.get("function") or {}
                    if function.get("name"):
                        entry["name"] += str(function["name"])
                    if function.get("arguments"):
                        entry["arguments"] += str(function["arguments"])
                if chunk.usage:
                    usage.update(chunk.usage)

        try:
            asyncio.run(pump())
        except Exception as exc:  # noqa: BLE001
            if content or fragments:
                raise
            logger.info("research: streaming unavailable (%s); plain call", exc)
            raise _StreamFellBack() from exc
        tool_calls = [
            {
                "id": fragments[i]["id"] or f"call_{i}",
                "name": fragments[i]["name"],
                "arguments": fragments[i]["arguments"] or "{}",
            }
            for i in sorted(fragments)
        ]
        if tool_calls and content:
            self._emit({"type": "synthesis_replace", "text": ""})
        return {"content": "".join(content), "tool_calls": tool_calls, "usage": usage}

    def _execute_clarify(self, args: Dict[str, Any]) -> ToolInvocation:
        question = str(args.get("question", "") or "").strip()
        if not question:
            return ToolInvocation(
                tool_name="clarify",
                arguments={"question": ""},
                response="(no question provided by agent — skipping clarify)",
            )
        answer = self._clarify_handler(question)
        return ToolInvocation(
            tool_name="clarify",
            arguments={"question": question},
            response=answer,
        )

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    def _resolve_available_sources(self) -> List[str]:
        """Return the source IDs the user actually has data for.

        Override > live query of the KnowledgeStore. Failure to read the
        store (e.g. no _store attribute on the search backend) returns
        ``[]`` so the prompt still formats — better empty than crashing.
        """
        if self._available_sources_override is not None:
            return list(self._available_sources_override)
        store = getattr(self._search, "_store", None)
        if store is None:
            return []
        try:
            return list(store.distinct_sources())
        except Exception as exc:  # noqa: BLE001
            logger.debug("distinct_sources() failed: %s", exc)
            return []

    def run(self, query: str) -> ResearchResult:
        """Run the loop end-to-end and return the synthesis plus a trace."""
        # The pages a search returns become readable for this question, and
        # the read budget counts from it (security/page_access.py).
        from openjarvis.security import page_access

        with page_access.scope(query):
            return self._run(query)

    def _run(self, query: str) -> ResearchResult:
        started = time.monotonic()
        sources_list = self._resolve_available_sources()
        if sources_list:
            sources_blurb = ", ".join(sources_list)
        else:
            sources_blurb = (
                "(no connected sources — tell the user to connect a "
                "connector before searching)"
            )
        now = datetime.now()
        sys_msg = Message(
            role=Role.SYSTEM,
            content=SYSTEM_PROMPT.format(
                today=now.isoformat(timespec="minutes"),
                today_weekday=now.strftime("%A"),
                available_sources=sources_blurb,
                web_tools_section=_WEB_TOOLS_SECTION if self._web_search else "",
                web_read_section=(
                    _WEB_READ_SECTION if self._web_search and self._web_read else ""
                ),
                tool_budget=self._max_iterations,
                web_tools_strategy=(
                    _WEB_TOOLS_STRATEGY.format(
                        max_web_searches=self._max_web_searches,
                        web_read_strategy=(
                            _WEB_READ_STRATEGY.format(
                                max_reads=getattr(self._web_read, "_max_reads", 3)
                            )
                            if self._web_read
                            else ""
                        ),
                    )
                    if self._web_search
                    else ""
                ),
                web_tools_synthesis=(
                    _WEB_TOOLS_SYNTHESIS if self._web_search else ""
                ),
            ),
        )
        messages: List[Message] = [sys_msg, Message(role=Role.USER, content=query)]

        invocations: List[ToolInvocation] = []
        web_searches = 0
        web_queries: set = set()
        told_time_up = False
        nudged_to_search = False
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # Global ref counter: each search increments by the number of hits
        # it returned so the planner sees unique refs across calls. The
        # accumulator lets us renumber whatever the synthesis cites at the
        # end into a single deduped client-facing sources list.
        next_ref: int = 1
        ref_to_source: Dict[int, Dict[str, Any]] = {}

        def _finalize(text: str) -> Tuple[str, List[Dict[str, Any]]]:
            return renumber_citations(text, ref_to_source)

        iterations = 0
        for _ in range(self._max_iterations + 1):
            iterations += 1
            in_time = (
                self._time_budget is None
                or time.monotonic() - started < self._time_budget
            )
            if len(invocations) < self._max_iterations and in_time:
                tools_arg = [SEARCH_TOOL_SPEC, CLARIFY_TOOL_SPEC]
                if (
                    self._web_search is not None
                    and web_searches < self._max_web_searches
                ):
                    web_spec = self._web_search.to_openai_function()
                    tools_arg.append(
                        web_spec if isinstance(web_spec, dict) else WEB_SEARCH_TOOL_SPEC
                    )
                if (
                    self._web_read is not None
                    and web_searches
                    and not _reads_spent(self._web_read)
                ):
                    tools_arg.append(self._web_read.to_openai_function())
            else:
                tools_arg = None
                if not in_time and not told_time_up:
                    told_time_up = True
                    messages.append(
                        Message(
                            role=Role.USER,
                            content=(
                                "Research time is up. Write the final answer now"
                                " from what you found above; say what is still"
                                " uncertain."
                            ),
                        )
                    )
            result = self._generate(messages, tools_arg)
            for k in total_usage:
                total_usage[k] += int(result.get("usage", {}).get(k, 0))

            content = result.get("content", "") or ""
            tool_calls_raw = result.get("tool_calls", []) or []

            if not tool_calls_raw:
                if (
                    content.strip()
                    and tools_arg
                    and self._web_search is not None
                    and not web_searches
                    and not nudged_to_search
                    and not any(i.num_results for i in invocations)
                ):
                    # Answered from nothing: the corpus had nothing and the
                    # web was never searched. Once, ask for the search.
                    nudged_to_search = True
                    if self._stream:
                        self._emit({"type": "synthesis_replace", "text": ""})
                    messages.append(Message(role=Role.ASSISTANT, content=content))
                    messages.append(
                        Message(
                            role=Role.USER,
                            content=(
                                "You have not searched the web, and the personal"
                                " corpus does not hold this. Call web_search now"
                                " (several angles if useful), then answer."
                            ),
                        )
                    )
                    continue
                if content.strip():
                    answer, final_sources = _finalize(content.strip())
                    self._emit(
                        {
                            "type": "final_answer",
                            "text": answer,
                            "sources": final_sources,
                            "streamed": self._stream,
                        }
                    )
                    return ResearchResult(
                        answer=answer,
                        iterations=iterations,
                        tool_calls=invocations,
                        usage=total_usage,
                    )
                # Empty content with no tool call — push a synthesis prod
                if invocations:
                    messages.append(Message(role=Role.ASSISTANT, content=content))
                    messages.append(
                        Message(
                            role=Role.USER,
                            content=(
                                "Write your final answer now based on the search "
                                "results above. Cite sources as [1], [2], etc."
                            ),
                        )
                    )
                    continue
                fallback = "(model returned no content and no tool calls)"
                self._emit({"type": "final_answer", "text": fallback, "sources": []})
                return ResearchResult(
                    answer=fallback,
                    iterations=iterations,
                    tool_calls=invocations,
                    usage=total_usage,
                )

            assistant_msg = Message(
                role=Role.ASSISTANT,
                content=content,
                tool_calls=[
                    ToolCall(
                        id=tc.get("id", f"call_{i}"),
                        name=tc.get("name", "search"),
                        arguments=tc.get("arguments", "{}") or "{}",
                    )
                    for i, tc in enumerate(tool_calls_raw)
                ],
            )
            messages.append(assistant_msg)

            for tc in tool_calls_raw:
                name = tc.get("name", "")
                raw_args = tc.get("arguments", "{}") or "{}"
                try:
                    args = (
                        json.loads(raw_args)
                        if isinstance(raw_args, str)
                        else dict(raw_args)
                    )
                except json.JSONDecodeError:
                    args = {}

                if name == "search":
                    # Guard against the planner pre-empting clarify before any
                    # search has run — silently accept; the rule lives in the
                    # system prompt as guidance, not enforcement.
                    self._emit({"type": "search_call", "arguments": args})
                    inv = self._execute_search(args)
                    invocations.append(inv)
                    offset = next_ref - 1
                    sources_for_search = build_sources_for_client(
                        inv.raw_hits, ref_offset=offset
                    )
                    self._emit(
                        {
                            "type": "search_result",
                            "num_hits": inv.num_results,
                            "top_titles": inv.top_titles,
                            "sources": sources_for_search,
                        }
                    )
                    for src in sources_for_search:
                        ref_to_source[int(src["ref"])] = src
                    next_ref += len(sources_for_search)
                    tool_output = json.dumps(
                        shape_results_for_model(inv.raw_hits, ref_offset=offset),
                        ensure_ascii=False,
                    )
                elif name == "clarify":
                    # Enforce the "search first" rule at runtime so we don't
                    # surprise the user with a clarification before showing any
                    # work. If the planner jumps to clarify with no searches
                    # behind it, return an error and let the loop try again.
                    if not any(i.tool_name == "search" for i in invocations):
                        tool_output = json.dumps(
                            {
                                "error": (
                                    "clarify is only available after at least "
                                    "one search call. Run search first, then "
                                    "use clarify if the results are ambiguous "
                                    "or empty."
                                )
                            }
                        )
                    else:
                        self._emit(
                            {
                                "type": "clarify_call",
                                "question": str(args.get("question", "")),
                            }
                        )
                        inv = self._execute_clarify(args)
                        invocations.append(inv)
                        self._emit(
                            {"type": "clarify_response", "response": inv.response}
                        )
                        tool_output = json.dumps(
                            {
                                "question": inv.arguments.get("question", ""),
                                "user_response": inv.response,
                            }
                        )
                elif name == "web_search" and self._web_search is not None:
                    query_key = " ".join(str(args.get("query", "")).lower().split())
                    if web_searches >= self._max_web_searches:
                        tool_output = json.dumps(
                            {
                                "error": (
                                    "the web_search budget for this request is"
                                    " spent; synthesize from the results above"
                                )
                            }
                        )
                    elif query_key in web_queries:
                        tool_output = json.dumps(
                            {
                                "error": (
                                    "that exact query was already searched; its"
                                    " results are above. Search a different angle"
                                    " or answer."
                                )
                            }
                        )
                    else:
                        web_searches += 1
                        web_queries.add(query_key)
                        self._emit({"type": "web_search_call", "arguments": args})
                        inv = self._execute_web_search(args)
                        invocations.append(inv)
                        self._emit(
                            {
                                "type": "web_search_result",
                                "num_results": inv.num_results,
                                "sources": inv.sources,
                                "images": inv.images,
                                "explicit_image_search": inv.explicit_image_search,
                                "success": inv.success,
                            }
                        )
                        tool_output = inv.response
                elif name == "web_read" and self._web_read is not None:
                    self._emit({"type": "web_read_call", "arguments": args})
                    inv = self._execute_web_read(args)
                    invocations.append(inv)
                    self._emit(
                        {
                            "type": "web_read_result",
                            "pages_read": inv.num_results,
                            "sources": inv.sources,
                            "success": inv.success,
                        }
                    )
                    tool_output = inv.response
                else:
                    tool_output = json.dumps(
                        {
                            "error": (
                                f"unknown tool {name!r}; available tools are "
                                "'search', 'clarify'"
                                + (", 'web_search'" if self._web_search else "")
                            )
                        }
                    )

                messages.append(
                    Message(
                        role=Role.TOOL,
                        content=tool_output,
                        tool_call_id=tc.get("id", ""),
                        name=name,
                    )
                )

            if len(invocations) >= self._max_iterations:
                messages.append(
                    Message(
                        role=Role.USER,
                        content=(
                            "You have used your tool-call budget (search, "
                            "clarify, and web_search combined). Write the final"
                            " synthesis now using only the results and"
                            " clarifications above. Cite personal-corpus"
                            " sources as [1], [2], etc. and web sources as"
                            " their URLs."
                        ),
                    )
                )

        # Loop fell through without the model producing a text response.
        # Force one final tool-less synthesis call so the caller always gets
        # an answer — bailing out with a sentinel string is never useful to
        # the user, who already paid for the searches.
        messages.append(
            Message(
                role=Role.USER,
                content=(
                    "You've used all your search attempts. Synthesize your "
                    "findings now from whatever you've found so far. Do not "
                    "request more tool calls — write the final answer as "
                    "plain text, citing sources as [1], [2], etc. where you can. "
                    "If the searches returned nothing usable, say so plainly."
                ),
            )
        )
        iterations += 1
        final = self._generate(messages, None)
        for k in total_usage:
            total_usage[k] += int(final.get("usage", {}).get(k, 0))
        answer = (final.get("content", "") or "").strip()
        if not answer:
            answer = (
                "(no synthesis available — the search budget was exhausted "
                "and the model returned no text response)"
            )
        answer, final_sources = _finalize(answer)
        self._emit(
            {
                "type": "final_answer",
                "text": answer,
                "sources": final_sources,
                "streamed": self._stream,
            }
        )
        return ResearchResult(
            answer=answer,
            iterations=iterations,
            tool_calls=invocations,
            usage=total_usage,
        )


__all__ = [
    "ResearchAgent",
    "ResearchResult",
    "ToolInvocation",
    "SEARCH_TOOL_SPEC",
    "CLARIFY_TOOL_SPEC",
    "WEB_SEARCH_TOOL_SPEC",
    "SYSTEM_PROMPT",
    "DEFAULT_PLANNER_MODEL",
    "shape_results_for_model",
    "build_sources_for_client",
]
