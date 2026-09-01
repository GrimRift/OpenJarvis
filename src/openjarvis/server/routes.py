"""Route handlers for the OpenAI-compatible API server."""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import uuid
import weakref
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from openjarvis.core.paths import get_config_dir
from openjarvis.core.types import Message, Role, ToolCall
from openjarvis.server.model_capabilities import is_embed_only_model
from openjarvis.server.models import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ChoiceMessage,
    ComplexityInfo,
    DeltaMessage,
    ModelListResponse,
    ModelObject,
    StreamChoice,
    UsageInfo,
)

router = APIRouter()


def _has_attached_image(request_body) -> bool:
    """Whether the newest user turn carries an image.

    An image turn is answered in one step by the vision model itself — no tool
    loop, no hand-off. That is the scoped design, and it is also forced: the
    agent path reduces the last message to `req.messages[-1].content`, a bare
    string, so an image handed to it is silently dropped and the model answers
    "I can't see an image attached".
    """
    for message in reversed(list(getattr(request_body, "messages", None) or [])):
        if getattr(message, "role", "") == "user":
            return bool(getattr(message, "images", None))
    return False


def _to_messages(chat_messages) -> list[Message]:
    """Convert Pydantic ChatMessage objects to core Message objects."""
    messages = []
    for m in chat_messages:
        role = Role(m.role) if m.role in {r.value for r in Role} else Role.USER
        messages.append(
            Message(
                role=role,
                content=m.content or "",
                name=m.name,
                tool_calls=[
                    ToolCall(
                        id=tool_call.get("id", ""),
                        name=tool_call.get("function", {}).get("name", ""),
                        arguments=tool_call.get("function", {}).get("arguments", "{}"),
                    )
                    for tool_call in (m.tool_calls or [])
                ]
                or None,
                tool_call_id=m.tool_call_id,
            )
        )
        # Assigned after construction rather than passed in: Message has no
        # `images` field, and every engine reads it with getattr. Ephemeral by
        # design — images ride this request and are never indexed.
        if getattr(m, "images", None):
            messages[-1].images = list(m.images)
    return messages


def _ensure_identity_prompt(messages: list[Message], app_config) -> list[Message]:
    """Prepend OpenJarvis's identity system prompt when the client omits one.

    The desktop UI's chat backend posts only user/assistant turns to
    ``/v1/chat/completions`` (see ``frontend/.../Chat/InputArea.tsx``), so
    nothing grounds the model's identity. Without a system prompt the model
    answers from its training identity (e.g. "I'm Claude", "I am Qwen"),
    which is what #540 reported. The CLI paths inject this via
    ``SystemPromptBuilder`` / ``BaseAgent``; the engine-direct server paths
    did not. This mirrors the agent fallback in ``agents/_stubs.py``.

    If any caller-supplied message already carries a system role, the caller
    has supplied their own grounding and we leave the list untouched (no
    double-prompting). Internally tagged memory context does not count as
    caller grounding.

    Resolution of the identity text: the config comes from ``app.state`` when
    wired, otherwise ``load_config()``; the prompt itself is assembled by
    ``SystemPromptBuilder`` from ``agent.default_system_prompt`` plus the
    persona files (SOUL.md/MEMORY.md/USER.md), matching
    ``_build_managed_system_prompt`` in ``agent_manager_routes.py``. Config
    resolution is wrapped so a broken/missing config degrades to "no
    injection" rather than crashing the endpoint, but the failure is logged
    (per REVIEW.md — never silently swallow).
    """

    def _is_caller_system_prompt(m: Message) -> bool:
        return m.role == Role.SYSTEM and not m.metadata.get("memory_context")

    if any(_is_caller_system_prompt(m) for m in messages):
        return messages

    prompt = ""
    try:
        cfg = app_config
        if cfg is None:
            from openjarvis.core.config import load_config

            cfg = load_config()

        from openjarvis.prompt.builder import SystemPromptBuilder

        builder = SystemPromptBuilder(
            agent_template=(
                cfg.agent.system_prompt or cfg.agent.default_system_prompt or ""
            ),
            memory_files_config=getattr(cfg, "memory_files", None),
            system_prompt_config=getattr(cfg, "system_prompt", None),
        )
        prompt = builder.build()
    except Exception:
        logging.getLogger("openjarvis.server").debug(
            "Identity system prompt resolution failed; "
            "serving request without identity grounding",
            exc_info=True,
        )
        return messages

    if not prompt:
        return messages

    return [Message(role=Role.SYSTEM, content=prompt), *messages]


# Same pattern as system/orchestrator.py's QueryOrchestrator._detect_agent_intent
# — that class isn't reachable from this endpoint (the web chat UI calls this
# route directly against a single pre-built agent), so the regex is
# duplicated here rather than imported, matching the one already proven by
# tests/test_query_orchestrator.py::TestDetectAgentIntent.
_DIGEST_INTENT_RE = re.compile(
    r"\b(good\s+morning|morning\s+digest|daily\s+briefing|morning\s+briefing)\b",
    re.IGNORECASE,
)

# Bare Spotify transport commands ("next song", "skip", "pause") reliably
# get hallucinated by the configured model instead of actually calling
# spotify_control -- confirmed by checking real playback state before and
# after: the track never changed, even though the model confidently replied
# "Skipped to the next track." Unlike "play <song>", which needs a real
# track name only the tool can provide and so forces a genuine call, these
# need no specific facts to sound plausible, and a system-prompt warning
# against exactly this didn't change the behavior. Short and unambiguous
# enough to route deterministically instead of trusting the model's
# tool-choice for it -- same technique as _DIGEST_INTENT_RE above.
_SPOTIFY_TRANSPORT_RE = re.compile(
    r"^\s*(?:hey\s+sage[,.]?\s*)?"
    r"(?:please\s+)?"
    r"(?:"
    r"(?P<next>skip(?:\s+(?:this\s+)?(?:song|track))?|next(?:\s+(?:song|track))?)"
    r"|(?P<pause>pause(?:\s+(?:the\s+)?(?:music|song|spotify))?)"
    r"|(?P<previous>(?:go\s+)?back(?:\s+(?:a\s+)?(?:song|track))?"
    r"|previous(?:\s+(?:song|track))?)"
    r")"
    r"(?:,?\s+please)?[.!]?\s*$",
    re.IGNORECASE,
)


def _spotify_transport_action(text: str) -> Optional[str]:
    match = _SPOTIFY_TRANSPORT_RE.match(text)
    if not match:
        return None
    for action in ("next", "pause", "previous"):
        if match.group(action) is not None:
            return action
    return None


def _run_spotify_transport(action: str) -> str:
    from openjarvis.tools.spotify_control import SpotifyControlTool

    result = SpotifyControlTool().execute(action=action, query="")
    return result.content


@router.post("/v1/chat/completions")
async def chat_completions(request_body: ChatCompletionRequest, request: Request):
    """Handle chat completion requests (streaming and non-streaming)."""
    # Bind this turn before anything touches the messages: a tool that needs
    # confirmation is answered on the *next* turn, and the identity of this one
    # is what proves a real user reply happened in between. Computed from the
    # request as it arrived, ahead of memory injection, so the key does not
    # move when recalled facts change between the ask and the answer.
    # Each request runs in its own task, so this ContextVar is request-local.
    from openjarvis.security import confirmations

    confirmations.set_turn(request_body.messages)

    engine = request.app.state.engine
    agent = getattr(request.app.state, "agent", None)
    model = request_body.model

    # Inject memory context into messages before dispatching
    config = getattr(request.app.state, "config", None)
    memory_backend = getattr(request.app.state, "memory_backend", None)
    if (
        config is not None
        and config.agent.context_from_memory
        and request_body.messages
    ):
        try:
            from openjarvis.tools.storage.context import ContextConfig, inject_context

            memory_service = getattr(request.app.state, "memory_service", None)
            facts = memory_service.list_facts() if memory_service is not None else []

            # Extract query from the last user message
            query_text = ""
            for m in reversed(request_body.messages):
                if m.role == "user" and m.content:
                    query_text = m.content
                    break

            if query_text:
                messages = _to_messages(request_body.messages)
                messages = _ensure_identity_prompt(messages, config)
                ctx_cfg = ContextConfig(
                    top_k=config.memory.context_top_k,
                    min_score=config.memory.context_min_score,
                    max_context_tokens=config.memory.context_max_tokens,
                )
                enriched = inject_context(
                    query_text,
                    messages,
                    memory_backend,
                    config=ctx_cfg,
                    facts=facts,
                )
                # Rebuild after identity/context merging so downstream engine
                # adapters always receive exactly one system message.
                from openjarvis.server.models import ChatMessage

                new_msgs = []
                for msg in enriched:
                    new_msgs.append(
                        ChatMessage(
                            role=msg.role.value,
                            content=msg.content,
                            name=msg.name,
                            tool_calls=[
                                {
                                    "id": tool_call.id,
                                    "type": "function",
                                    "function": {
                                        "name": tool_call.name,
                                        "arguments": tool_call.arguments,
                                    },
                                }
                                for tool_call in (msg.tool_calls or [])
                            ]
                            or None,
                            tool_call_id=getattr(msg, "tool_call_id", None),
                            # Carried explicitly. This rebuild copies field by
                            # field, so anything not named here is dropped —
                            # which is how an attached image reached the model
                            # as "I can't see an image attached".
                            images=list(getattr(msg, "images", None) or []) or None,
                        )
                    )
                request_body.messages = new_msgs
        except Exception:
            logging.getLogger("openjarvis.server").debug(
                "Memory context injection failed",
                exc_info=True,
            )

    # Run complexity analysis on the last user message
    complexity_info = None
    query_text_for_complexity = ""
    for m in reversed(request_body.messages):
        if m.role == "user" and m.content:
            query_text_for_complexity = m.content
            break
    if query_text_for_complexity:
        try:
            from openjarvis.learning.routing.complexity import (
                adjust_tokens_for_model,
                score_complexity,
            )

            cr = score_complexity(query_text_for_complexity)
            suggested = adjust_tokens_for_model(
                cr.suggested_max_tokens,
                model,
            )
            complexity_info = ComplexityInfo(
                score=cr.score,
                tier=cr.tier,
                suggested_max_tokens=suggested,
            )
            # Bump max_tokens when complexity suggests more than what
            # the client requested — never reduce below the request value.
            if suggested > request_body.max_tokens:
                request_body.max_tokens = suggested
        except Exception:
            logging.getLogger("openjarvis.server").debug(
                "Complexity analysis failed",
                exc_info=True,
            )

    # Route digest-phrase queries to a fresh MorningDigestAgent instead of
    # the default configured agent. Sits before the tools/streaming branches
    # below and skips entirely when the client supplied their own `tools` —
    # those requests bypass agent routing per #414 regardless, so building a
    # digest agent for them would be pure waste (never consulted below).
    if (
        not request_body.tools
        and query_text_for_complexity
        and _DIGEST_INTENT_RE.search(query_text_for_complexity)
    ):
        from openjarvis.agents.morning_digest import build_morning_digest_agent

        digest_agent = build_morning_digest_agent(
            engine,
            model,
            config,
            bus=getattr(request.app.state, "bus", None),
            # The Web UI synthesizes digest audio after rendering the text.
            # Scheduled/channel digests keep the factory default and still
            # create their audio eagerly before delivery.
            generate_audio=False,
        )
        if digest_agent is not None:
            agent = digest_agent

    # Same reasoning as the digest block above, for a different unreliable
    # spot: a bare "next"/"skip"/"pause" reliably gets hallucinated by the
    # model instead of an actual spotify_control call (see
    # _SPOTIFY_TRANSPORT_RE's comment). Narrow on purpose -- only matches
    # the whole message, so it never intercepts "pause and think about
    # this" or a "play <song>" request the model still needs to handle.
    if not request_body.tools and query_text_for_complexity:
        transport_action = _spotify_transport_action(query_text_for_complexity)
        if transport_action is not None:
            content = await asyncio.to_thread(_run_spotify_transport, transport_action)
            if request_body.stream:
                chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

                async def generate_transport_reply():
                    first = ChatCompletionChunk(
                        id=chunk_id,
                        model=model,
                        choices=[StreamChoice(delta=DeltaMessage(role="assistant"))],
                    )
                    yield f"data: {first.model_dump_json()}\n\n"
                    body_delta = DeltaMessage(content=content)
                    body = ChatCompletionChunk(
                        id=chunk_id,
                        model=model,
                        choices=[StreamChoice(delta=body_delta, finish_reason="stop")],
                    )
                    yield f"data: {body.model_dump_json()}\n\n"
                    yield "data: [DONE]\n\n"

                return StreamingResponse(
                    generate_transport_reply(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
                )
            return ChatCompletionResponse(
                model=model,
                choices=[Choice(message=ChoiceMessage(content=content))],
                complexity=complexity_info,
            )

    if request_body.stream:
        # When the client passes `tools`, stream the model's raw
        # OpenAI-compat function-calling decision directly from the engine
        # (bypassing the agent) — the streaming mirror of the non-streaming
        # #454 fix. Routing client-supplied tools through a server-side agent
        # would execute the agent's different tool set and drop the raw tool
        # call the caller expects (#414).
        #
        # Without client-supplied tools, keep streaming requests on the
        # configured server agent so its server-side tool loop is available
        # to the desktop UI and other stream:true clients (#735). Fall back to
        # direct token streaming when no tool-bearing agent is configured.
        if request_body.tools:
            return await _handle_stream_tools(
                engine,
                model,
                request_body,
                complexity_info,
                app_config=config,
                bus=getattr(request.app.state, "bus", None),
                memory_service=getattr(request.app.state, "memory_service", None),
            )
        if (
            agent is not None
            and getattr(agent, "_tools", None)
            and not _has_attached_image(request_body)
        ):
            return await _handle_agent_stream(
                agent,
                model,
                request_body,
                complexity_info,
                trace_store=getattr(request.app.state, "trace_store", None),
                bus=getattr(request.app.state, "bus", None),
                memory_service=getattr(request.app.state, "memory_service", None),
            )
        return await _handle_stream(
            engine,
            model,
            request_body,
            complexity_info,
            trace_store=getattr(request.app.state, "trace_store", None),
            app_config=config,
            bus=getattr(request.app.state, "bus", None),
            memory_service=getattr(request.app.state, "memory_service", None),
        )

    # Non-streaming: use agent if available, otherwise direct engine call.
    #
    # EXCEPTION: when the client explicitly passed `tools`, they're asking
    # for raw OpenAI-compat function-calling — return the model's
    # tool_call decision verbatim. Routing through `_handle_agent` would
    # call `agent.run(input_text)`, which IGNORES `request_body.tools`,
    # runs the agent's own internal tool loop with its own (different)
    # tool spec, and returns only `result.content` — so the model's
    # tool_calls vanish and the user sees a generic acknowledgement
    # (e.g. "Understood. If you have another request...") that the
    # agent's re-prompted LLM produced. See #414.
    #
    # If a future caller needs agent orchestration WITH client-supplied
    # tools (e.g. injecting MCP tools through this endpoint and wanting
    # the agent to execute them), add an explicit opt-in header rather
    # than removing this guard — silent re-routing is what produced #414.
    # ``_handle_agent`` (sync ``agent.run()``) and ``_handle_direct`` (sync
    # ``engine.generate()``) both make blocking upstream calls; run them in a
    # worker thread so a slow/wedged non-streaming request can't stall the
    # event loop and every other concurrent request with it.
    if (
        agent is not None
        and not request_body.tools
        and not _has_attached_image(request_body)
    ):
        response = await asyncio.to_thread(
            _handle_agent,
            agent,
            model,
            request_body,
            complexity_info,
            trace_store=getattr(request.app.state, "trace_store", None),
            bus=getattr(request.app.state, "bus", None),
        )
    else:
        bus = getattr(request.app.state, "bus", None)
        response = await asyncio.to_thread(
            _handle_direct,
            engine,
            model,
            request_body,
            bus=bus,
            complexity_info=complexity_info,
            app_config=config,
        )

    # Hand the completed exchange to the background memory service.
    _remember_exchange(
        getattr(request.app.state, "memory_service", None),
        query_text_for_complexity,
        response,
        bus=getattr(request.app.state, "bus", None),
        source="server.chat",
    )
    return response


def _response_content(response) -> str:
    """Extract assistant text from an OpenAI-compatible response object."""
    content = ""
    choices = getattr(response, "choices", None)
    if choices:
        content = getattr(choices[0].message, "content", "") or ""
    return content


def _record_completed_exchange(
    memory_service,
    user_text: str,
    assistant_text: str,
    *,
    bus=None,
    source: str = "server.chat",
) -> None:
    """Publish or submit a completed exchange without blocking a reply."""
    if not user_text:
        return
    try:
        if bus is not None:
            from openjarvis.memory import publish_completed_exchange

            publish_completed_exchange(
                bus,
                user_text,
                assistant_text,
                source=source,
            )
        elif memory_service is not None:
            memory_service.submit(user_text, assistant_text)
    except Exception:  # noqa: BLE001 — memory is best-effort, never fail a reply
        logging.getLogger("openjarvis.server").debug(
            "Memory submit failed",
            exc_info=True,
        )


def _remember_exchange(
    memory_service,
    user_text: str,
    response,
    *,
    bus=None,
    source: str = "server.chat",
) -> None:
    """Record a completed non-streaming exchange."""
    _record_completed_exchange(
        memory_service,
        user_text,
        _response_content(response),
        bus=bus,
        source=source,
    )


def _engine_key_for_model(engine: Any, model: str) -> str | None:
    """Resolve the engine that advertised *model* through wrapper layers."""
    from openjarvis.engine.multi import MultiEngine
    from openjarvis.security.guardrails import GuardrailsEngine
    from openjarvis.telemetry.instrumented_engine import InstrumentedEngine

    current = engine
    while current is not None:
        if isinstance(current, MultiEngine):
            return current.engine_key_for(model)
        if isinstance(current, InstrumentedEngine):
            current = current._inner
            continue
        if isinstance(current, GuardrailsEngine):
            current = current._engine
            continue
        engine_id = getattr(current, "engine_id", None)
        return engine_id if isinstance(engine_id, str) else None
    return None


def _uses_direct_cloud_router(engine: Any, model: str) -> bool:
    """Whether *model* should bypass the configured engine for direct cloud."""
    from openjarvis.server.cloud_router import is_cloud_model

    return is_cloud_model(model) and _engine_key_for_model(engine, model) != "litellm"


def _handle_direct(
    engine,
    model: str,
    req: ChatCompletionRequest,
    bus=None,
    complexity_info=None,
    app_config=None,
) -> ChatCompletionResponse:
    """Direct engine call without agent."""
    messages = _to_messages(req.messages)
    messages = _ensure_identity_prompt(messages, app_config)
    kwargs: dict[str, Any] = {}
    if req.tools:
        kwargs["tools"] = req.tools
    if bus:
        from openjarvis.telemetry.instrumented_engine import InstrumentedEngine
        from openjarvis.telemetry.wrapper import instrumented_generate

        # `app.state.engine` may already be an InstrumentedEngine (the
        # common case when telemetry is wired in). If we then wrap it
        # with `instrumented_generate`, BOTH layers fire a
        # TELEMETRY_RECORD per call:
        #
        #   - InstrumentedEngine.generate() publishes a FULL record
        #     (energy_joules, GPU stats, token_counting_version, ...).
        #   - instrumented_generate() publishes a BARE record (timing +
        #     tokens only; no energy meter, no version stamp).
        #
        # The doubled count was the dominant driver of the bimodal
        # Wh/token distribution on the public leaderboard.
        #
        # The fix below is NOT "unwrap and call instrumented_generate":
        # that would have replaced "doubled records" with "every
        # request emits only a bare record with no energy / no version",
        # which the leaderboard's `current_methodology_only=True` filter
        # would then drop entirely. Instead, when the engine is already
        # an InstrumentedEngine, skip the wrapper and call `generate`
        # directly — InstrumentedEngine publishes the full per-record
        # event itself with energy + version intact. Only fall back to
        # the lightweight wrapper for engines that aren't already
        # instrumented.
        if isinstance(engine, InstrumentedEngine):
            result = engine.generate(
                messages,
                model=model,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                **kwargs,
            )
        else:
            result = instrumented_generate(
                engine,
                messages,
                model=model,
                bus=bus,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                **kwargs,
            )
    else:
        result = engine.generate(
            messages,
            model=model,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            **kwargs,
        )
    content = result.get("content", "")
    usage = result.get("usage", {})

    choice_msg = ChoiceMessage(role="assistant", content=content)
    # Include tool calls if present
    tool_calls = result.get("tool_calls")
    if tool_calls:
        choice_msg.tool_calls = [
            {
                "id": tc.get("id", ""),
                "type": "function",
                "function": {
                    "name": tc.get("name", ""),
                    "arguments": tc.get("arguments", "{}"),
                },
            }
            for tc in tool_calls
        ]

    return ChatCompletionResponse(
        model=model,
        choices=[
            Choice(
                message=choice_msg,
                finish_reason=result.get("finish_reason", "stop"),
            )
        ],
        usage=UsageInfo(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        ),
        complexity=complexity_info,
    )


_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MARKDOWN_MARKER_RE = re.compile(r"[*_#`]+")
_MARKDOWN_BULLET_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)


def _clean_for_speech(text: str) -> str:
    """Strip common markdown syntax so TTS doesn't read literal symbols aloud."""
    text = _MARKDOWN_LINK_RE.sub(r"\1", text)
    text = _MARKDOWN_MARKER_RE.sub("", text)
    text = _MARKDOWN_BULLET_RE.sub("", text)
    return text.strip()


def _synthesize_reply_audio(text: str):
    """Speak a voice-originated turn's reply back, deterministically.

    Reuses the same TextToSpeechTool and token-based serving already used
    by POST /v1/speech/synthesize + GET /v1/speech/audio/{token} — just
    invoked directly here instead of via a model tool call. That mirrors
    the lesson already applied to notify_class_schedule: a small local
    model can't be trusted to remember "call text_to_speech" on every
    voice-originated turn, so the decision is made in code instead.
    """
    from openjarvis.server.api_routes import _SYNTHESIZED_AUDIO
    from openjarvis.server.models import AudioMeta
    from openjarvis.tools.text_to_speech import TextToSpeechTool

    tool = TextToSpeechTool()
    try:
        result = tool.execute(text=_clean_for_speech(text))
    except Exception:
        logging.getLogger("openjarvis.server").warning(
            "Voice-reply TTS synthesis failed", exc_info=True
        )
        return None

    if not result.success:
        logging.getLogger("openjarvis.server").warning(
            "Voice-reply TTS synthesis failed: %s", result.content
        )
        return None

    token = uuid.uuid4().hex
    _SYNTHESIZED_AUDIO[token] = result.metadata["audio_path"]
    return AudioMeta(url=f"/v1/speech/audio/{token}")


# _handle_agent runs on a worker thread (via asyncio.to_thread) for both
# the streaming and non-streaming routes, so concurrent requests against
# the same shared agent execute on real OS threads and can genuinely
# interleave. A lock per agent *instance* serializes the
# override-run-restore critical section below so one request's temporary
# `model` override can never be read as another's "original" value (#759).
# Key by object identity rather than by the agent itself: custom agents may be
# unhashable or may not support weak references.  The values are weak so this
# registry retains neither the agent nor an idle lock.  A caller keeps the lock
# alive from lookup through the full override/run/restore critical section.
_agent_model_locks: "weakref.WeakValueDictionary[int, threading.Lock]" = (
    weakref.WeakValueDictionary()
)
_agent_model_locks_guard = threading.Lock()


def _get_agent_model_lock(agent: Any) -> threading.Lock:
    """Return the lock serializing model overrides for *agent*, creating it
    on first use. One lock per agent instance, not global, so unrelated
    agents don't serialize against each other."""
    agent_id = id(agent)
    with _agent_model_locks_guard:
        lock = _agent_model_locks.get(agent_id)
        if lock is None:
            lock = threading.Lock()
            _agent_model_locks[agent_id] = lock
        return lock


def _handle_agent(
    agent,
    model: str,
    req: ChatCompletionRequest,
    complexity_info=None,
    *,
    trace_store=None,
    bus=None,
) -> ChatCompletionResponse:
    """Run through agent.

    When *trace_store* is set, the agent run is wrapped in a
    ``TraceCollector`` (mirroring ``system/orchestrator.py``) so every
    completion records a ``Trace`` to ``traces.db``. Previously this endpoint
    called ``agent.run()`` raw, so the server never produced traces:
    ``traces.db`` stayed empty and spec_search's cold-start gate
    (``check_readiness``, min 20 traces) could never open.
    """
    from openjarvis.agents._stubs import AgentContext

    # Build context from prior messages
    ctx = AgentContext()
    if len(req.messages) > 1:
        prior = _to_messages(req.messages[:-1])
        for m in prior:
            ctx.conversation.add(m)

    # Last message is the input
    input_text = req.messages[-1].content if req.messages else ""

    # Override agent model for this request if the caller specified one.
    # Locked for the full override-run-restore cycle (#759): only the
    # override/restore lines racing wouldn't be enough, since agent.run()
    # itself reads self._model throughout the call.
    with _get_agent_model_lock(agent):
        original_model = agent._model
        if model:
            agent._model = model
        try:
            if trace_store is not None:
                from openjarvis.traces.collector import TraceCollector

                collector = TraceCollector(agent, store=trace_store, bus=bus)
                result = collector.run(input_text, context=ctx)
            else:
                result = agent.run(input_text, context=ctx)
        finally:
            agent._model = original_model

    usage = UsageInfo(
        prompt_tokens=result.metadata.get("prompt_tokens", 0),
        completion_tokens=result.metadata.get("completion_tokens", 0),
        total_tokens=result.metadata.get("total_tokens", 0),
    )

    # Include audio metadata if the agent produced audio (e.g. morning digest)
    audio_meta = None
    audio_path = result.metadata.get("audio_path", "")
    if audio_path:
        from pathlib import Path

        from openjarvis.server.models import AudioMeta

        if Path(audio_path).exists():
            audio_meta = AudioMeta(url="/api/digest/audio")

    # A voice-originated turn that didn't already get audio from a tool
    # (e.g. this isn't a digest request) gets its reply spoken back too.
    if audio_meta is None and req.voice and result.content:
        audio_meta = _synthesize_reply_audio(result.content)

    response = ChatCompletionResponse(
        model=model,
        choices=[
            Choice(
                message=ChoiceMessage(
                    role="assistant",
                    content=result.content,
                    audio=audio_meta,
                ),
                finish_reason="stop",
            )
        ],
        usage=usage,
        complexity=complexity_info,
    )
    # Carried out-of-band rather than added to the OpenAI-shaped body, which
    # has no field for "tools the server ran on your behalf". The streaming
    # wrapper turns these into tool events so the client can record that the
    # turn used tools; see _handle_agent_stream.
    response._agent_tool_results = list(result.tool_results or [])
    return response


def _merge_agent_tool_call_fragments(
    accumulated: dict[int, dict[str, Any]],
    fragments: list[dict[str, Any]],
) -> None:
    """Merge OpenAI-style streaming tool-call fragments by index."""
    for fragment in fragments:
        index = int(fragment.get("index", 0))
        if index not in accumulated:
            accumulated[index] = {
                "id": fragment.get("id", "") or f"call_{index}",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            }
        entry = accumulated[index]
        if fragment.get("id"):
            entry["id"] = fragment["id"]
        function = fragment.get("function", {}) or {}
        if function.get("name"):
            entry["function"]["name"] += str(function["name"])
        if function.get("arguments"):
            entry["function"]["arguments"] += str(function["arguments"])


# Tools that a bounded/terminal search result retires for the rest of the turn.
_SEARCH_TOOL_NAMES = frozenset({"web_search"})


async def _handle_streaming_orchestrator(
    agent,
    model: str,
    req: ChatCompletionRequest,
    complexity_info=None,
    *,
    trace_store=None,
    bus=None,
    memory_service=None,
):
    """Stream an OrchestratorAgent's real final-turn model deltas.

    This is the async counterpart of ``OrchestratorAgent._run_function_calling``:
    it keeps the same prompt builder, routed server-side tool set, loop guard,
    and tool executor, while using ``engine.stream_full`` instead of waiting for
    ``engine.generate``.  The browser therefore receives stable text while the
    final answer is still being produced, which lets incremental TTS overlap
    model generation without bypassing Sage's tools or persona.
    """
    import json as _json
    import time

    from openjarvis.agents._stubs import AgentContext
    from openjarvis.core.types import ToolResult

    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    input_text = req.messages[-1].content if req.messages else ""
    query_text = input_text or ""

    context = AgentContext()
    if len(req.messages) > 1:
        for message in _to_messages(req.messages[:-1]):
            context.conversation.add(message)

    messages = agent._build_messages(
        input_text,
        context,
        system_prompt=agent._system_prompt,
    )
    if agent._loop_guard:
        agent._loop_guard.reset()
        messages = agent._trim_history_once(messages)

    openai_tools = agent._executor.get_openai_tools() if agent._tools else []
    if openai_tools and agent._route_tools:
        from openjarvis.agents.tool_routing import route_tools, routing_text

        openai_tools = route_tools(
            openai_tools,
            routing_text(input_text, context.conversation.messages),
        )

    telemetry_engine = _engine_key_for_model(agent._engine, model) or getattr(
        agent._engine, "engine_id", ""
    )

    async def generate():
        started_at = time.time()
        active_tools = list(openai_tools)
        full_content = ""
        all_tool_results = []
        total_prompt_tokens = 0
        total_completion_tokens = 0
        turns = 0

        agent._emit_turn_start(input_text)
        first_chunk = ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[StreamChoice(delta=DeltaMessage(role="assistant"))],
        )
        yield f"data: {first_chunk.model_dump_json()}\n\n"

        try:
            while turns < agent._max_turns:
                turns += 1
                if agent._loop_guard:
                    messages[:] = agent._loop_guard.compress_context(
                        messages,
                        apply_token_budget=False,
                    )

                turn_content = ""
                tool_fragments: dict[int, dict[str, Any]] = {}
                finish_reason = "stop"
                turn_usage: dict[str, Any] = {}
                stream_kwargs: dict[str, Any] = {}
                if active_tools:
                    stream_kwargs["tools"] = active_tools

                async for stream_chunk in agent._engine.stream_full(
                    messages,
                    model=model or agent._model,
                    temperature=agent._temperature,
                    max_tokens=agent._max_tokens,
                    **stream_kwargs,
                ):
                    if stream_chunk.content:
                        turn_content += stream_chunk.content
                        content_chunk = ChatCompletionChunk(
                            id=chunk_id,
                            model=model,
                            choices=[
                                StreamChoice(
                                    delta=DeltaMessage(content=stream_chunk.content)
                                )
                            ],
                        )
                        yield f"data: {content_chunk.model_dump_json()}\n\n"
                    if stream_chunk.tool_calls:
                        _merge_agent_tool_call_fragments(
                            tool_fragments,
                            stream_chunk.tool_calls,
                        )
                    if stream_chunk.finish_reason:
                        finish_reason = stream_chunk.finish_reason
                    if stream_chunk.usage:
                        turn_usage = stream_chunk.usage

                total_prompt_tokens += int(turn_usage.get("prompt_tokens", 0) or 0)
                total_completion_tokens += int(
                    turn_usage.get("completion_tokens", 0) or 0
                )

                if tool_fragments:
                    ordered = [
                        tool_fragments[index] for index in sorted(tool_fragments)
                    ]
                    tool_calls = [
                        ToolCall(
                            id=item["id"],
                            name=item["function"]["name"],
                            arguments=item["function"]["arguments"] or "{}",
                        )
                        for item in ordered
                    ]
                    messages.append(
                        Message(
                            role=Role.ASSISTANT,
                            content=turn_content,
                            tool_calls=tool_calls,
                        )
                    )

                    results_by_index: dict[int, ToolResult] = {}
                    pending: list[tuple[int, ToolCall]] = []
                    for index, tool_call in enumerate(tool_calls):
                        start_payload = _json.dumps(
                            {
                                "id": tool_call.id,
                                "tool": tool_call.name,
                                "arguments": tool_call.arguments,
                            }
                        )
                        yield f"event: tool_call_start\ndata: {start_payload}\n\n"
                        if agent._loop_guard:
                            verdict = agent._loop_guard.check_call(
                                tool_call.name,
                                tool_call.arguments,
                            )
                            if verdict.blocked:
                                results_by_index[index] = ToolResult(
                                    tool_name=tool_call.name,
                                    content=f"Loop guard: {verdict.reason}",
                                    success=False,
                                )
                                continue
                        pending.append((index, tool_call))

                    if agent._parallel_tools and len(pending) > 1:
                        executed = await asyncio.gather(
                            *[
                                asyncio.to_thread(agent._executor.execute, tool_call)
                                for _, tool_call in pending
                            ]
                        )
                        for (index, _), tool_result in zip(
                            pending, executed, strict=True
                        ):
                            results_by_index[index] = tool_result
                    else:
                        for index, tool_call in pending:
                            results_by_index[index] = await asyncio.to_thread(
                                agent._executor.execute,
                                tool_call,
                            )

                    for index, tool_call in enumerate(tool_calls):
                        tool_result = results_by_index[index]
                        if agent._loop_guard and index in {
                            pending_index for pending_index, _ in pending
                        }:
                            agent._loop_guard.record_result(
                                tool_call.name,
                                tool_call.arguments,
                                tool_result.success,
                            )
                        all_tool_results.append(tool_result)
                        messages.append(
                            Message(
                                role=Role.TOOL,
                                content=tool_result.content,
                                tool_call_id=tool_call.id,
                                name=tool_call.name,
                            )
                        )
                        end_payload = _json.dumps(
                            {
                                "id": tool_call.id,
                                "tool": tool_call.name,
                                "success": bool(tool_result.success),
                                "result": str(tool_result.content)[:500],
                                "metadata": (
                                    {
                                        "sources": tool_result.metadata.get(
                                            "sources", []
                                        ),
                                        "images": tool_result.metadata.get(
                                            "images", []
                                        ),
                                        "explicit_image_search": (
                                            tool_result.metadata.get(
                                                "explicit_image_search", False
                                            )
                                        ),
                                    }
                                    if isinstance(tool_result.metadata, dict)
                                    else {}
                                ),
                            }
                        )
                        yield f"event: tool_call_end\ndata: {end_payload}\n\n"
                    if any(
                        isinstance(result.metadata, dict)
                        and (
                            result.metadata.get("terminal_search") is True
                            or result.metadata.get("bounded_search_complete") is True
                        )
                        for result in results_by_index.values()
                    ):
                        # Only the search tool is spent. Clearing the whole list
                        # also stripped spotify_control, notify_windows and the
                        # file tools, so "search for X, then play it" lost its
                        # second tool halfway through the turn.
                        active_tools = [
                            tool
                            for tool in active_tools
                            if (tool.get("function") or {}).get("name")
                            not in _SEARCH_TOOL_NAMES
                        ]
                    continue

                full_content += turn_content

                # Preserve the sync agent's bounded continuation recovery.
                for _ in range(2):
                    if finish_reason != "length":
                        break
                    messages.append(Message(role=Role.ASSISTANT, content=full_content))
                    messages.append(
                        Message(
                            role=Role.USER,
                            content="Continue from where you left off.",
                        )
                    )
                    finish_reason = "stop"
                    continuation = ""
                    async for stream_chunk in agent._engine.stream_full(
                        messages,
                        model=model or agent._model,
                        temperature=agent._temperature,
                        max_tokens=agent._max_tokens,
                    ):
                        if stream_chunk.content:
                            continuation += stream_chunk.content
                            content_chunk = ChatCompletionChunk(
                                id=chunk_id,
                                model=model,
                                choices=[
                                    StreamChoice(
                                        delta=DeltaMessage(
                                            content=stream_chunk.content
                                        )
                                    )
                                ],
                            )
                            yield f"data: {content_chunk.model_dump_json()}\n\n"
                        if stream_chunk.finish_reason:
                            finish_reason = stream_chunk.finish_reason
                        if stream_chunk.usage:
                            total_prompt_tokens += int(
                                stream_chunk.usage.get("prompt_tokens", 0) or 0
                            )
                            total_completion_tokens += int(
                                stream_chunk.usage.get("completion_tokens", 0) or 0
                            )
                    full_content += continuation
                break
            else:
                if not full_content:
                    full_content = "Maximum turns reached without a final answer."
                    content_chunk = ChatCompletionChunk(
                        id=chunk_id,
                        model=model,
                        choices=[
                            StreamChoice(delta=DeltaMessage(content=full_content))
                        ],
                    )
                    yield f"data: {content_chunk.model_dump_json()}\n\n"

        except Exception as exc:
            logging.getLogger("openjarvis.server").error(
                "Orchestrator stream error: %s",
                exc,
                exc_info=True,
            )
            error_chunk = ChatCompletionChunk(
                id=chunk_id,
                model=model,
                choices=[
                    StreamChoice(
                        delta=DeltaMessage(
                            content=f"Sorry, an error occurred: {exc}",
                        ),
                        finish_reason="stop",
                    )
                ],
            )
            yield f"data: {error_chunk.model_dump_json()}\n\n"
            yield "data: [DONE]\n\n"
            return

        agent._emit_turn_end(turns=turns, content_length=len(full_content))
        ended_at = time.time()
        if trace_store is not None and full_content:
            from openjarvis.traces.collector import record_response_trace

            record_response_trace(
                trace_store,
                query=query_text,
                result=full_content,
                model=model,
                engine=telemetry_engine,
                agent=agent.agent_id,
                started_at=started_at,
                ended_at=ended_at,
            )

        if full_content:
            _record_completed_exchange(
                memory_service,
                query_text,
                full_content,
                bus=bus,
                source="server.chat.stream",
            )

        # Some streaming providers (including the currently configured OpenAI
        # path) do not attach usage to their final delta. The old synchronous
        # agent response always carried token counts, so falling through as
        # zeros made the UI's input/output token figures disappear. Preserve
        # exact provider usage when present and use the same conservative
        # estimator as the rest of the server only when it is absent.
        if total_prompt_tokens <= 0:
            from openjarvis.engine._base import estimate_prompt_tokens

            total_prompt_tokens = estimate_prompt_tokens(messages)
        if total_completion_tokens <= 0 and full_content:
            total_completion_tokens = max(1, len(full_content) // 4)

        finish_chunk = ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[StreamChoice(delta=DeltaMessage(), finish_reason="stop")],
        )
        finish_data = _json.loads(finish_chunk.model_dump_json())
        finish_data["usage"] = UsageInfo(
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_prompt_tokens + total_completion_tokens,
        ).model_dump()
        finish_data.setdefault("telemetry", {})
        finish_data["telemetry"]["engine"] = telemetry_engine
        if complexity_info is not None:
            finish_data["complexity"] = complexity_info.model_dump()
        yield f"data: {_json.dumps(finish_data)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


async def _handle_agent_stream(
    agent,
    model: str,
    req: ChatCompletionRequest,
    complexity_info=None,
    *,
    trace_store=None,
    bus=None,
    memory_service=None,
):
    """Run the configured agent and return its result as an SSE response.

    Agents own the tool-execution loop, which is synchronous today.  Run that
    loop in a worker thread and stream its final answer once complete.  This
    keeps ``stream:true`` clients (including the desktop UI) on the same agent
    and configured toolkit as non-streaming requests instead of bypassing the
    agent and silently dropping server-side tools.

    Requests that explicitly supply OpenAI ``tools`` continue to use
    ``_handle_stream_tools`` so their raw tool-call deltas are preserved.
    """
    from openjarvis.agents.orchestrator import OrchestratorAgent

    if isinstance(agent, OrchestratorAgent) and agent._mode == "function_calling":
        return await _handle_streaming_orchestrator(
            agent,
            model,
            req,
            complexity_info,
            trace_store=trace_store,
            bus=bus,
            memory_service=memory_service,
        )

    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    query_text = ""
    for message in reversed(req.messages):
        if message.role == "user" and message.content:
            query_text = message.content
            break

    async def generate():
        first_chunk = ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[StreamChoice(delta=DeltaMessage(role="assistant"))],
        )
        yield f"data: {first_chunk.model_dump_json()}\n\n"

        try:
            # The browser can display the completed text immediately and
            # already starts voice-reply TTS asynchronously when no audio is
            # attached to the finish event.  Suppress the synchronous TTS in
            # _handle_agent for streaming voice turns; otherwise a several-
            # second media request delays the text the user could already be
            # reading. Any agent-produced audio still comes through
            # result.metadata and remains attached normally; interactive Web
            # digests deliberately use the same deferred browser path.
            agent_req = req.model_copy(update={"voice": False})
            response = await asyncio.to_thread(
                _handle_agent,
                agent,
                model,
                agent_req,
                complexity_info,
                trace_store=trace_store,
                bus=bus,
            )
        except Exception as exc:
            logging.getLogger("openjarvis.server").error(
                "Agent stream error: %s",
                exc,
                exc_info=True,
            )
            error_chunk = ChatCompletionChunk(
                id=chunk_id,
                model=model,
                choices=[
                    StreamChoice(
                        delta=DeltaMessage(
                            content=f"Sorry, an error occurred: {exc}",
                        ),
                        finish_reason="stop",
                    )
                ],
            )
            yield f"data: {error_chunk.model_dump_json()}\n\n"
            yield "data: [DONE]\n\n"
            return

        import json as _json

        # The agent's tool loop runs to completion in a worker thread, so
        # these are reported after the fact rather than live. Emitting them
        # at all is what lets the client know the turn used tools: without
        # it, a tool-using turn is indistinguishable from a plain answer,
        # and replaying that history back makes the model treat "open the
        # app" as something answered with a sentence — it then repeats the
        # sentence and opens nothing.
        for index, tool_result in enumerate(
            getattr(response, "_agent_tool_results", []) or []
        ):
            name = getattr(tool_result, "tool_name", "") or "tool"
            call_id = f"{chunk_id}-tool-{index}"
            # "{}" rather than "": this string is stored by the client and
            # replayed as a tool call's `arguments` on later turns, and an
            # empty one is not parseable JSON — Ollama rejects the whole
            # request with "Value looks like object, but can't find closing
            # '}' symbol", breaking every message after the first tool use.
            # The real arguments are not recoverable here (the tool loop has
            # already finished), so an empty object stands in.
            start = _json.dumps({"id": call_id, "tool": name, "arguments": "{}"})
            yield f"event: tool_call_start\ndata: {start}\n\n"
            end = _json.dumps(
                {
                    "id": call_id,
                    "tool": name,
                    "success": bool(getattr(tool_result, "success", True)),
                    "result": str(getattr(tool_result, "content", ""))[:500],
                    "metadata": (
                        {
                            "sources": getattr(tool_result, "metadata", {}).get(
                                "sources", []
                            ),
                            "images": getattr(tool_result, "metadata", {}).get(
                                "images", []
                            ),
                            "explicit_image_search": getattr(
                                tool_result, "metadata", {}
                            ).get("explicit_image_search", False),
                        }
                        if isinstance(getattr(tool_result, "metadata", {}), dict)
                        else {}
                    ),
                }
            )
            yield f"event: tool_call_end\ndata: {end}\n\n"

        content = _response_content(response)
        if content:
            content_chunk = ChatCompletionChunk(
                id=chunk_id,
                model=model,
                choices=[StreamChoice(delta=DeltaMessage(content=content))],
            )
            yield f"data: {content_chunk.model_dump_json()}\n\n"

        finish_chunk = ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[
                StreamChoice(delta=DeltaMessage(), finish_reason="stop"),
            ],
        )
        finish_data = _json.loads(finish_chunk.model_dump_json())
        finish_data["usage"] = response.usage.model_dump()
        if complexity_info is not None:
            finish_data["complexity"] = complexity_info.model_dump()
        response_audio = response.choices[0].message.audio
        if response_audio is not None:
            finish_data["audio"] = response_audio.model_dump()
        yield f"data: {_json.dumps(finish_data)}\n\n"

        _record_completed_exchange(
            memory_service,
            query_text,
            content,
            bus=bus,
            source="server.chat.stream",
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


async def _handle_stream_tools(
    engine,
    model: str,
    req: ChatCompletionRequest,
    complexity_info=None,
    *,
    app_config=None,
    bus=None,
    memory_service=None,
):
    """Stream a raw OpenAI-compat function-calling response via SSE.

    Used when the client passes `tools` together with `stream:true`.  Sources
    tool_calls from ``engine.stream_full()`` (which forwards the tools to the
    backend and parses tool_calls out of the streamed response) and emits them
    as SSE deltas, bypassing the agent entirely.  This is the streaming mirror
    of the non-streaming ``_handle_direct`` tool path.

    Engines without a tool-aware ``stream_full`` override fall back to the
    base-class default (content tokens + a ``stop`` finish_reason, no
    tool_calls) — identical to the prior plain-stream behaviour, so this never
    regresses non-tool-capable engines.
    """
    messages = _to_messages(req.messages)
    messages = _ensure_identity_prompt(messages, app_config)
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    use_cloud = _uses_direct_cloud_router(engine, model)
    telemetry_engine = (
        "cloud" if use_cloud else (_engine_key_for_model(engine, model) or "ollama")
    )
    query_text = ""
    for _m in reversed(req.messages):
        if _m.role == "user" and _m.content:
            query_text = _m.content
            break

    async def generate():
        full_content = ""
        # Send the role chunk first (OpenAI convention).
        first_chunk = ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[StreamChoice(delta=DeltaMessage(role="assistant"))],
        )
        yield f"data: {first_chunk.model_dump_json()}\n\n"

        finish_reason = "stop"
        try:
            async for sc in engine.stream_full(
                messages,
                model=model,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                tools=req.tools,
            ):
                if sc.content:
                    full_content += sc.content
                    content_chunk = ChatCompletionChunk(
                        id=chunk_id,
                        model=model,
                        choices=[StreamChoice(delta=DeltaMessage(content=sc.content))],
                    )
                    yield f"data: {content_chunk.model_dump_json()}\n\n"
                if sc.tool_calls:
                    tc_chunk = ChatCompletionChunk(
                        id=chunk_id,
                        model=model,
                        choices=[
                            StreamChoice(delta=DeltaMessage(tool_calls=sc.tool_calls))
                        ],
                    )
                    yield f"data: {tc_chunk.model_dump_json()}\n\n"
                if sc.finish_reason:
                    finish_reason = sc.finish_reason
        except Exception as exc:
            import logging

            logging.getLogger("openjarvis.server").error(
                "Tool stream error: %s",
                exc,
                exc_info=True,
            )
            error_chunk = ChatCompletionChunk(
                id=chunk_id,
                model=model,
                choices=[
                    StreamChoice(
                        delta=DeltaMessage(
                            content=f"\n\nError during generation: {exc}",
                        ),
                        finish_reason="stop",
                    )
                ],
            )
            yield f"data: {error_chunk.model_dump_json()}\n\n"
            yield "data: [DONE]\n\n"
            return

        import json as _json

        finish_data = ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[StreamChoice(delta=DeltaMessage(), finish_reason=finish_reason)],
        )
        finish_dict = _json.loads(finish_data.model_dump_json())
        # Tag the finish chunk with the engine label, matching _handle_stream
        # so UI/telemetry consumers see the same field on the tools path.
        finish_dict.setdefault("telemetry", {})
        finish_dict["telemetry"]["engine"] = telemetry_engine
        if complexity_info is not None:
            finish_dict["complexity"] = complexity_info.model_dump()
        yield f"data: {_json.dumps(finish_dict)}\n\n"
        if full_content:
            _record_completed_exchange(
                memory_service,
                query_text,
                full_content,
                bus=bus,
                source="server.chat.stream",
            )
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


async def _handle_stream(
    engine,
    model: str,
    req: ChatCompletionRequest,
    complexity_info=None,
    *,
    trace_store=None,
    app_config=None,
    bus=None,
    memory_service=None,
):
    """Stream response using SSE format.

    This no-agent fallback streams straight from the engine, bypassing the
    ``TraceCollector``. When *trace_store* is set we accumulate the streamed
    tokens and record a minimal ``Trace`` once the stream completes
    successfully.
    """
    import time

    from openjarvis.server.cloud_router import stream_cloud, stream_local

    messages = _to_messages(req.messages)
    messages = _ensure_identity_prompt(messages, app_config)
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    # Last user message — recorded as the trace query.
    query_text = ""
    for _m in reversed(req.messages):
        if _m.role == "user" and _m.content:
            query_text = _m.content
            break

    # Route directly to the right backend — bypasses engine routing entirely
    # so broken MultiEngine state can never misdirect requests.
    use_cloud = _uses_direct_cloud_router(engine, model)
    telemetry_engine = (
        "cloud" if use_cloud else (_engine_key_for_model(engine, model) or "ollama")
    )

    async def generate():
        started_at = time.time()
        full_content = ""
        # Send role chunk first
        first_chunk = ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[
                StreamChoice(
                    delta=DeltaMessage(role="assistant"),
                )
            ],
        )
        yield f"data: {first_chunk.model_dump_json()}\n\n"

        try:
            # Cloud models → direct cloud API (reads keys from disk).
            # Local models → engine.stream() first so mock engines work in
            # tests.  Fall back to stream_local() only when the engine would
            # mis-route the request to a cloud backend (MultiEngine routing
            # confusion), which is detected by checking the routed engine's
            # is_cloud attribute.
            if use_cloud:
                token_iter = stream_cloud(
                    model, messages, req.temperature, req.max_tokens
                )
            else:
                # Use engine.stream() by default (preserves mock-engine
                # compatibility in tests).  Only fall back to stream_local()
                # when a real MultiEngine would mis-route the local model to a
                # cloud backend — detected via isinstance so mocks are not
                # accidentally matched.
                _use_local_fallback = False
                try:
                    from openjarvis.engine.multi import MultiEngine

                    _inner = getattr(engine, "_inner", engine)
                    if isinstance(_inner, MultiEngine):
                        _routed = _inner._engine_for(model)
                        if _routed is not None and getattr(_routed, "is_cloud", False):
                            _use_local_fallback = True
                except Exception:
                    pass
                if _use_local_fallback:
                    token_iter = stream_local(
                        model, messages, req.temperature, req.max_tokens
                    )
                else:
                    token_iter = engine.stream(
                        messages,
                        model=model,
                        temperature=req.temperature,
                        max_tokens=req.max_tokens,
                    )
            async for token in token_iter:
                full_content += token
                chunk = ChatCompletionChunk(
                    id=chunk_id,
                    model=model,
                    choices=[
                        StreamChoice(
                            delta=DeltaMessage(content=token),
                        )
                    ],
                )
                yield f"data: {chunk.model_dump_json()}\n\n"
        except Exception as exc:
            # Surface errors as a content chunk so the frontend can
            # display them instead of silently failing.
            import logging

            logging.getLogger("openjarvis.server").error(
                "Stream error: %s",
                exc,
                exc_info=True,
            )
            error_chunk = ChatCompletionChunk(
                id=chunk_id,
                model=model,
                choices=[
                    StreamChoice(
                        delta=DeltaMessage(
                            content=f"\n\nError during generation: {exc}",
                        ),
                        finish_reason="stop",
                    )
                ],
            )
            yield f"data: {error_chunk.model_dump_json()}\n\n"
            yield "data: [DONE]\n\n"
            return

        # Record a trace for the completed stream (best-effort; never breaks
        # the response). Mirrors the agent path so streamed chats also
        # populate traces.db.
        if trace_store is not None and full_content:
            from openjarvis.traces.collector import record_response_trace

            record_response_trace(
                trace_store,
                query=query_text,
                result=full_content,
                model=model,
                engine=telemetry_engine,
                started_at=started_at,
                ended_at=time.time(),
            )

        if full_content:
            _record_completed_exchange(
                memory_service,
                query_text,
                full_content,
                bus=bus,
                source="server.chat.stream",
            )

        # Send finish chunk with usage data if available
        import json as _json

        finish_data = ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[
                StreamChoice(
                    delta=DeltaMessage(),
                    finish_reason="stop",
                )
            ],
        )
        finish_dict = _json.loads(finish_data.model_dump_json())

        # Tag the finish chunk with the correct engine label.
        # We use the routing decision (use_cloud) directly rather than
        # unwrapping the engine chain, which can be in a broken state.
        finish_dict.setdefault("telemetry", {})
        finish_dict["telemetry"]["engine"] = telemetry_engine

        if complexity_info is not None:
            finish_dict["complexity"] = complexity_info.model_dump()

        yield f"data: {_json.dumps(finish_dict)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.get("/v1/models")
async def list_models(request: Request) -> ModelListResponse:
    """List selectable engine models for the installed-model picker.

    Direct cloud models live in the Cloud Models tab. Models advertised by a
    configured LiteLLM engine remain here because LiteLLM owns their routing
    and may use provider-qualified IDs that resemble OpenRouter IDs.
    """
    from openjarvis.server.cloud_router import is_cloud_model, list_local_models

    # Prefer engine.list_models() so mock engines work in tests.
    # Filter out direct-cloud model IDs that may appear via MultiEngine, but
    # retain provider-qualified IDs owned by the configured LiteLLM engine.
    # Fall back to direct Ollama query only when the engine returns nothing.
    engine = request.app.state.engine
    all_ids = await asyncio.to_thread(engine.list_models)
    model_ids = [
        m
        for m in all_ids
        if not is_cloud_model(m) or _engine_key_for_model(engine, m) == "litellm"
    ]
    if not model_ids:
        model_ids = await list_local_models()

    # Keep embed-only models out of the chat model picker. They still work for
    # memory/retrieval via the embedder path; putting them in /v1/models made
    # the UI auto-select nomic-embed-text and fail every generation with 400.
    model_ids = [m for m in model_ids if not is_embed_only_model(m)]

    return ModelListResponse(
        data=[
            ModelObject(
                id=mid,
                owned_by=(
                    "litellm"
                    if _engine_key_for_model(engine, mid) == "litellm"
                    else "openjarvis"
                ),
            )
            for mid in model_ids
        ],
    )


@router.post("/v1/models/pull")
async def pull_model(request: Request):
    """Pull / download a model from the Ollama registry."""
    body = await request.json()
    model_name = body.get("model", "").strip()
    if not model_name:
        raise HTTPException(status_code=400, detail="'model' field is required")

    engine = request.app.state.engine
    engine_name = getattr(request.app.state, "engine_name", "")
    # Only Ollama supports pulling
    if engine_name != "ollama" and getattr(engine, "engine_id", "") != "ollama":
        raise HTTPException(
            status_code=501,
            detail="Model pulling is only supported with the Ollama engine",
        )

    import httpx as _httpx

    host = getattr(engine, "_host", "http://localhost:11434")
    try:
        async with _httpx.AsyncClient(base_url=host, timeout=600.0) as client:
            resp = await client.post(
                "/api/pull",
                json={"name": model_name, "stream": False},
            )
        resp.raise_for_status()
    except (_httpx.ConnectError, _httpx.TimeoutException) as exc:
        raise HTTPException(status_code=502, detail=f"Ollama unreachable: {exc}")
    except _httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Ollama error: {exc.response.text[:300]}",
        )

    return {"status": "ok", "model": model_name}


@router.delete("/v1/models/{model_name:path}")
async def delete_model(model_name: str, request: Request):
    """Delete a model from Ollama."""
    engine = request.app.state.engine
    engine_name = getattr(request.app.state, "engine_name", "")
    if engine_name != "ollama" and getattr(engine, "engine_id", "") != "ollama":
        raise HTTPException(status_code=501, detail="Only supported with Ollama engine")

    import httpx as _httpx

    host = getattr(engine, "_host", "http://localhost:11434")
    try:
        async with _httpx.AsyncClient(base_url=host, timeout=30.0) as client:
            resp = await client.request(
                "DELETE",
                "/api/delete",
                json={"name": model_name},
            )
        resp.raise_for_status()
    except (_httpx.ConnectError, _httpx.TimeoutException) as exc:
        raise HTTPException(status_code=502, detail=f"Ollama unreachable: {exc}")
    except _httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Ollama error: {exc.response.text[:300]}",
        )

    return {"status": "deleted", "model": model_name}


@router.post("/v1/cloud/reload")
async def reload_cloud_engine(request: Request):
    """Hot-reload cloud API keys and (re-)initialize the cloud engine.

    Called by the desktop app immediately after the user saves a cloud API
    key so that cloud models become available without a full app restart.
    """
    import os

    submitted_keys: dict[str, str] | None = None
    try:
        body = await request.json()
        raw_keys = body.get("keys") if isinstance(body, dict) else None
        if isinstance(raw_keys, dict):
            submitted_keys = {
                str(k): str(v)
                for k, v in raw_keys.items()
                if str(k).endswith("_API_KEY")
            }
    except Exception:
        submitted_keys = None

    if submitted_keys is not None:
        for key, value in submitted_keys.items():
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
    else:
        # Compatibility fallback for non-desktop/manual configurations.
        keys_path = get_config_dir() / "cloud-keys.env"
        if keys_path.exists():
            for raw_line in keys_path.read_text().splitlines():
                line = raw_line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

    # Try to build a fresh CloudEngine.
    try:
        from openjarvis.engine.cloud import CloudEngine
        from openjarvis.engine.multi import MultiEngine

        cloud = CloudEngine()
        if not cloud.health():
            return {
                "status": "no_cloud",
                "message": "No cloud models available (check API keys)",
            }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}

    # Locate the innermost engine, working through InstrumentedEngine layers.
    outer = request.app.state.engine
    inner = getattr(outer, "_inner", outer)

    if isinstance(inner, MultiEngine):
        # Replace or insert the cloud entry in the existing MultiEngine.
        new_engines = [(k, e) for k, e in inner._engines if k != "cloud"]
        new_engines.append(("cloud", cloud))
        inner._engines = new_engines
        inner._refresh_map()
    else:
        # Wrap the existing engine (which may be security-wrapped) with a new
        # MultiEngine that includes the cloud engine.
        engine_name = getattr(request.app.state, "engine_name", "local")
        new_multi = MultiEngine([(engine_name, inner), ("cloud", cloud)])
        if hasattr(outer, "_inner"):
            outer._inner = new_multi
        else:
            request.app.state.engine = new_multi
        request.app.state.engine_name = "multi"

    return {"status": "ok", "message": "Cloud engine reloaded"}


@router.get("/v1/savings")
async def savings(request: Request):
    """Return savings summary compared to cloud providers.

    Only includes telemetry from the current server session so that
    counters start at zero each time a new model + agent is launched.
    """
    from openjarvis.core.config import DEFAULT_CONFIG_DIR
    from openjarvis.server.savings import compute_savings, savings_to_dict
    from openjarvis.telemetry.aggregator import TelemetryAggregator

    db_path = DEFAULT_CONFIG_DIR / "telemetry.db"
    if not db_path.exists():
        empty = compute_savings(0, 0, 0)
        return savings_to_dict(empty)

    session_start = getattr(request.app.state, "session_start", None)

    agg = TelemetryAggregator(db_path)
    try:
        # current_methodology_only excludes pre-fix legacy rows from
        # the leaderboard's per-token efficiency numerator/denominator
        # — see the comment on _time_filter for the bimodal-Wh/token
        # background.
        summary = agg.summary(since=session_start, current_methodology_only=True)
        # Exclude cloud model tokens from savings — only local
        # inference counts toward cost savings.
        _cloud_prefixes = (
            "gpt-",
            "o1-",
            "o3-",
            "o4-",
            "claude-",
            "gemini-",
            "openrouter/",
        )
        local_models = [
            m
            for m in summary.per_model
            if not any(m.model_id.startswith(p) for p in _cloud_prefixes)
        ]
        result = compute_savings(
            prompt_tokens=sum(m.prompt_tokens for m in local_models),
            completion_tokens=sum(m.completion_tokens for m in local_models),
            total_calls=sum(m.call_count for m in local_models),
            session_start=session_start if session_start else 0.0,
            prompt_tokens_evaluated=sum(
                m.prompt_tokens_evaluated for m in local_models
            ),
        )
        return savings_to_dict(result)
    finally:
        agg.close()


@router.post("/v1/telemetry/reset")
async def reset_telemetry():
    """Clear all stored telemetry records.

    Useful after updating token-counting methodology — clears
    historical records that were computed under the old rules so
    that the savings dashboard and leaderboard submissions start
    fresh with corrected values.
    """
    from openjarvis.core.config import DEFAULT_CONFIG_DIR
    from openjarvis.telemetry.aggregator import TelemetryAggregator

    db_path = DEFAULT_CONFIG_DIR / "telemetry.db"
    if not db_path.exists():
        return {"status": "ok", "records_cleared": 0}

    agg = TelemetryAggregator(db_path)
    try:
        count = agg.clear()
    finally:
        agg.close()
    return {"status": "ok", "records_cleared": count}


@router.get("/v1/info")
async def server_info(request: Request):
    """Return server configuration: model, agent, engine."""
    agent = getattr(request.app.state, "agent", None)
    agent_id = getattr(agent, "agent_id", None) if agent else None
    # Fall back to configured agent name if agent didn't instantiate
    if agent_id is None:
        agent_id = getattr(request.app.state, "agent_name", None)
    return {
        "model": getattr(request.app.state, "model", ""),
        "agent": agent_id,
        "engine": getattr(request.app.state, "engine_name", ""),
    }


@router.get("/health")
async def health(request: Request):
    """Health check endpoint."""
    engine = request.app.state.engine
    healthy = engine.health()
    if not healthy:
        raise HTTPException(status_code=503, detail="Engine unhealthy")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Channel endpoints
# ---------------------------------------------------------------------------


@router.get("/v1/channels")
async def list_channels(request: Request):
    """List available messaging channels."""
    bridge = getattr(request.app.state, "channel_bridge", None)
    if bridge is None:
        return {"channels": [], "message": "Channel bridge not configured"}
    channels = bridge.list_channels()
    return {"channels": channels, "status": bridge.status().value}


@router.post("/v1/channels/send")
async def channel_send(request: Request):
    """Send a message to a channel."""
    bridge = getattr(request.app.state, "channel_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=503, detail="Channel bridge not configured")

    body = await request.json()
    channel_name = body.get("channel", "")
    content = body.get("content", "")
    conversation_id = body.get("conversation_id", "")

    if not channel_name or not content:
        raise HTTPException(
            status_code=400,
            detail="'channel' and 'content' are required",
        )

    ok = bridge.send(channel_name, content, conversation_id=conversation_id)
    if not ok:
        raise HTTPException(status_code=502, detail="Failed to send message")
    return {"status": "sent", "channel": channel_name}


@router.get("/v1/channels/status")
async def channel_status(request: Request):
    """Return channel bridge connection status."""
    bridge = getattr(request.app.state, "channel_bridge", None)
    if bridge is None:
        return {"status": "not_configured"}
    return {"status": bridge.status().value}


# ---------------------------------------------------------------------------
# Security scan endpoint
# ---------------------------------------------------------------------------


@router.get("/v1/security/scan")
async def security_scan():
    """Run a read-only security environment audit and return findings."""
    from openjarvis.cli.scan_cmd import PrivacyScanner

    scanner = PrivacyScanner()
    results = scanner.run_all()
    return {
        "has_warnings": any(r.status == "warn" for r in results),
        "has_failures": any(r.status == "fail" for r in results),
        "findings": [
            {
                "name": r.name,
                "status": r.status,
                "message": r.message,
                "platform": r.platform,
            }
            for r in results
        ],
    }


__all__ = ["router"]
