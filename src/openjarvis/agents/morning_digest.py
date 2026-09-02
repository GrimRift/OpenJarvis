"""Morning Digest Agent — synthesizes a daily briefing from multiple sources.

Thin orchestrator that delegates to digest_collect (data fetching),
the LLM (narrative synthesis), and text_to_speech (audio generation).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from openjarvis.agents._model_override import apply_configured_model
from openjarvis.agents._stubs import AgentContext, AgentResult, ToolUsingAgent
from openjarvis.agents.digest_store import DigestArtifact, DigestStore
from openjarvis.core.config import load_config
from openjarvis.core.paths import get_config_dir
from openjarvis.core.registry import AgentRegistry
from openjarvis.core.types import Message, Role, ToolCall

logger = logging.getLogger(__name__)

# Floor for the single retry after an empty generation. A briefing is ~200
# words, so this is headroom for a reasoning model's thinking, not for output.
_EMPTY_RETRY_MAX_TOKENS = 4096

_SECTION_PROMPTS = {
    "messages": "MESSAGES — Prioritize provided messages or tasks needing action.",
    "calendar": "CALENDAR — Cover only provided upcoming events.",
    "health": "HEALTH — Describe only supported trends; omit raw measurements.",
    "world": "WORLD — Summarize only provided world items.",
    "music": "MUSIC — Summarize only provided listening information.",
    "notes": "NOTES — Briefly mention only the provided recently-edited notes.",
    "teams": (
        "TEAMS — Cover mentions and replies needing a response, and state "
        "every assignment with its due date. Never drop a due date."
    ),
}

# Sources read through the browser rather than a connector.
#
# ``digest_collect`` works off ConnectorRegistry, and the Outlook connector on
# this account reports ``is_connected: False`` — Microsoft's API is not
# available here, which is the whole reason ``outlook_read`` scrapes the page
# instead. Teams has no connector at all. So both are collected as ordinary
# tool calls and appended to the same evidence block the model is shown.
#
# Outlook folds into MESSAGES beside Gmail; Teams is its own section, because
# an assignment due date buried inside a mail summary is the one thing the
# user most needs not to lose.
_BROWSER_SOURCES = (
    ("messages", "outlook_read", "OUTLOOK MAIL"),
    ("teams", "teams_read", "MICROSOFT TEAMS"),
)


def _load_persona(persona_name: str) -> str:
    """Load a persona prompt file by name."""
    search_paths = [
        Path("configs/openjarvis/prompts/personas") / f"{persona_name}.md",
        get_config_dir() / "prompts" / "personas" / f"{persona_name}.md",
    ]
    for p in search_paths:
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


def _load_global_soul() -> str:
    """Load the same live Sage persona used by web and channel agents."""
    path = get_config_dir() / "SOUL.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


@AgentRegistry.register("morning_digest")
class MorningDigestAgent(ToolUsingAgent):
    """Pre-compute a daily digest from configured data sources."""

    agent_id = "morning_digest"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Extract digest-specific kwargs before passing to parent
        self._persona = kwargs.pop("persona", "jarvis")
        self._sections = kwargs.pop(
            "sections", ["messages", "calendar", "health", "world"]
        )
        self._section_sources = kwargs.pop("section_sources", {})
        self._timezone = kwargs.pop("timezone", "America/Los_Angeles")
        self._voice_id = kwargs.pop("voice_id", "")
        self._voice_speed = kwargs.pop("voice_speed", 1.0)
        self._voice_volume = kwargs.pop("voice_volume", 1.0)
        self._tts_backend = kwargs.pop("tts_backend", "cartesia")
        self._generate_audio = bool(kwargs.pop("generate_audio", True))
        self._digest_store_path = kwargs.pop("digest_store_path", "")
        self._honorific = kwargs.pop("honorific", "sir")

        # The scheduled 05:00 run wrote a visibly worse briefing than the same
        # digest asked for from the Web UI, which sends the chat model: on the
        # full evidence set (Gmail plus Outlook, Teams and a required deadline
        # preamble) the small local default overran the 200-word limit by 30%
        # and dropped Gmail entirely, while the cloud model covered every
        # source in 155 words. Same code, same data, different model.
        configured_model = ""
        configured_engine = ""
        try:
            digest_cfg = load_config().digest
            configured_model = digest_cfg.model
            configured_engine = digest_cfg.engine
        except Exception:
            pass
        args, kwargs = apply_configured_model(
            args, kwargs, configured_model, configured_engine, label="Digest"
        )

        super().__init__(*args, **kwargs)

    def _build_system_prompt(self) -> str:
        """Assemble the system prompt from persona + briefing structure."""
        persona_text = _load_persona(self._persona)
        global_soul = _load_global_soul()
        now = datetime.now()
        honorific = getattr(self, "_honorific", "sir")
        sections = dict.fromkeys(
            str(section).strip().casefold()
            for section in self._sections
            if str(section).strip()
        )
        section_block = "\n".join(
            f"- {_SECTION_PROMPTS.get(section, section.upper())}"
            for section in sections
        )

        return (
            f"{global_soul}\n\n"
            f"{persona_text}\n\n"
            f"Today is {now.strftime('%A, %B %d, %Y')}. "
            f"The time is {now.strftime('%I:%M %p')} in {self._timezone}.\n"
            f"The user's preferred honorific is: {honorific}\n\n"
            "You receive structured data from the user's connected services. "
            "The data has ALREADY been collected — it appears in the user "
            "message. You do NOT fetch anything yourself.\n\n"
            "Produce a concise spoken briefing in decreasing order of importance. "
            "Cover only the configured sections below and only when the collected "
            "data supports them. Silently omit absent data and sources.\n\n"
            f"CONFIGURED SECTIONS:\n{section_block or '- None'}\n\n"
            "Open briefly with the honorific and end after the last supported item. "
            "Do not add conversational offers or personal asides.\n\n"
            "ABSOLUTE RULES (violations are unacceptable):\n"
            "- ONLY facts from the data. Zero hallucination.\n"
            # A briefing that spends its budget on who reacted to a message
            # and then runs out before the assignment due tomorrow has failed
            # at the one thing it was asked to do. Observed: the assignment
            # was collected and simply never made it into the 200 words.
            "- DEADLINES FIRST. State every assignment, due date and deadline "
            "in the data before anything else, and never drop one for length.\n"
            "- NEVER mention disconnected or unavailable sources. Do not say "
            "a source had nothing, was unavailable, or was not reported — "
            "stay silent about it and spend the words on what is there.\n"
            "- NEVER invent personal context or claim, offer, or suggest actions.\n"
            "- Acknowledge every source that returned data, even briefly.\n"
            "- No markdown, emojis, bullets, or headers.\n"
            "- STRICT LIMIT: 200 words. Be concise."
        )

    def _resolve_sources(self) -> List[str]:
        """Get the list of connector IDs to query."""
        default_source_map = {
            "messages": [
                "gmail",
                "slack",
                "google_tasks",
                "imessage",
                "github_notifications",
            ],
            "calendar": ["gcalendar"],
            "health": ["oura", "apple_health"],
            "world": ["weather", "hackernews", "news_rss"],
            "music": ["spotify", "apple_music"],
            "notes": ["obsidian"],
        }
        sources = set()
        for section in self._sections:
            section_sources = self._section_sources.get(
                section, default_source_map.get(section, [])
            )
            sources.update(section_sources)
        return list(sources)

    def _collect_browser_sources(self) -> List[tuple]:
        """Read Outlook and Teams, which have no working connector.

        Each is skipped unless its section is configured, so turning off
        ``teams`` in config also stops paying for the slowest source. A failure
        is dropped rather than raised: a briefing missing one section is worth
        more than no briefing, and the model is told to omit absent sources
        anyway.
        """
        wanted = {
            str(section).strip().casefold()
            for section in self._sections
            if str(section).strip()
        }
        gathered = []
        for section, tool_name, label in _BROWSER_SOURCES:
            if section not in wanted:
                continue
            try:
                result = _run_browser_tool(tool_name)
            except Exception:
                logger.warning("%s failed for the digest", tool_name, exc_info=True)
                continue
            if result is not None and result.success and (result.content or "").strip():
                gathered.append((label, result))
            else:
                logger.info(
                    "%s returned nothing for the digest: %s",
                    tool_name,
                    (getattr(result, "content", "") or "")[:200],
                )
        logger.info("digest browser sources: %s", [label for label, _ in gathered])
        return gathered

    def _generate_narrative(self, messages: List[Message]) -> str:
        """Generate the briefing, retrying once with real headroom if empty.

        A reasoning model can spend its entire completion budget thinking and
        return no content at all: one live ``gpt-5.6-luna`` digest consumed
        exactly 1,024 tokens and finished with ``finish_reason="length"``
        having produced nothing, and the very next attempt succeeded. The same
        shape occurs when a model puts everything inside ``<think>`` tags,
        which strip to empty.

        Bounded to a single retry, and deliberately reports emptiness rather
        than papering over it -- the caller must be able to tell "the model
        said nothing" apart from "there was nothing to say".
        """
        result = self._generate(messages)
        narrative = self._strip_think_tags(result.get("content", ""))
        if narrative:
            return narrative

        headroom = max(self._max_tokens * 4, _EMPTY_RETRY_MAX_TOKENS)
        if headroom <= self._max_tokens:
            return ""

        logger.warning(
            "Digest generation returned no content (finish_reason=%r, "
            "max_tokens=%s); retrying once with %s.",
            result.get("finish_reason", ""),
            self._max_tokens,
            headroom,
        )
        retry = self._generate(messages, max_tokens=headroom)
        return self._strip_think_tags(retry.get("content", ""))

    def run(
        self,
        input: str,
        context: Optional[AgentContext] = None,
        **kwargs: Any,
    ) -> AgentResult:
        self._emit_turn_start(input)

        # Step 1: Collect data from connectors
        sources = self._resolve_sources()
        collect_call = ToolCall(
            id="digest-collect-1",
            name="digest_collect",
            arguments=json.dumps({"sources": sources, "hours_back": 24}),
        )
        collect_result = self._executor.execute(collect_call)
        collected_data = _without_errors(collect_result.content)
        browser_results = self._collect_browser_sources()
        for label, result in browser_results:
            collected_data = f"{collected_data}\n\n[{label}]\n{result.content}"
        deadlines = _deadline_lines(browser_results)

        # Step 2: Synthesize narrative via LLM
        system_prompt = self._build_system_prompt()
        messages = [
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(
                role=Role.USER,
                content=(
                    "The following collected data is the only factual evidence for "
                    f"the briefing:\n\n<collected_data>\n{collected_data}\n"
                    "</collected_data>\n\n"
                    # Stated here, outside the evidence block, because inside
                    # it the model read the instruction as more data and
                    # skipped the assignment three runs running — leading
                    # instead with a mail item it mistook for a deadline.
                    + (
                        "REQUIRED: begin the briefing with these deadlines, each "
                        f"with its own date, before any other item:\n{deadlines}\n\n"
                        if deadlines
                        else ""
                    )
                    + "Use configured sections only. Omit missing "
                    "data and sources. Do not add personal context or activities. "
                    "Use the honorific no more than three times and keep the "
                    "briefing under 200 words."
                ),
            ),
        ]

        narrative = self._generate_narrative(messages)
        if not narrative:
            # An empty generation is not an empty news day. Delivering it as
            # one would read as "nothing happened", which is the single
            # summary a user acts on by doing nothing. Stop before the
            # artifact is stored or spoken.
            self._emit_turn_end(turns=1)
            return AgentResult(
                content=(
                    "I could not generate your briefing — the model returned "
                    "nothing, twice. This is not a report that you have "
                    "nothing waiting."
                ),
                tool_results=[collect_result],
                turns=1,
                metadata={"error": "empty_generation", "sources_used": sources},
            )

        preamble = _deadline_preamble(deadlines, narrative)
        if preamble:
            narrative = preamble + "\n\n" + narrative

        # Step 2b: Self-evaluate and optionally regenerate
        quality_score = 0.0
        evaluator_feedback = ""
        try:
            from openjarvis.agents.digest_evaluator import DigestEvaluator

            evaluator = DigestEvaluator(self._engine, self._model)
            quality_score, evaluator_feedback = evaluator.evaluate(
                collected_data, narrative
            )

            if quality_score < 7.0 and evaluator_feedback:
                # Regenerate with feedback
                messages.append(
                    Message(
                        role=Role.USER,
                        content=(
                            f"Your briefing scored {quality_score:.1f}/10. "
                            f"Feedback: {evaluator_feedback}\n"
                            f"Please revise the briefing addressing this feedback."
                        ),
                    )
                )
                # Keep the original if the revision comes back empty --
                # a failed rewrite must not cost a briefing that was merely
                # scored low.
                revised = self._generate_narrative(messages)
                if revised:
                    narrative = revised
        except Exception:  # noqa: BLE001
            pass  # Evaluator failure shouldn't block digest delivery

        # Step 3: Generate audio via TTS
        from openjarvis.speech.spoken_text import to_spoken_text

        # Was three inline regexes covering headings, bullets and emphasis but
        # not tables, which a speech backend reads as a run of "vertical bar".
        tts_text = to_spoken_text(narrative)

        tts_result = None
        audio_path = ""
        if self._generate_audio:
            output_dir = str(get_config_dir() / "digests")
            tts_call = ToolCall(
                id="digest-tts-1",
                name="text_to_speech",
                arguments=json.dumps(
                    {
                        "text": tts_text,
                        "voice_id": self._voice_id,
                        "backend": self._tts_backend,
                        "speed": self._voice_speed,
                        "volume": self._voice_volume,
                        "output_dir": output_dir,
                    }
                ),
            )
            tts_result = self._executor.execute(tts_call)
            audio_path = (
                tts_result.metadata.get("audio_path", "") if tts_result.success else ""
            )

        # Step 4: Store the artifact
        artifact = DigestArtifact(
            text=narrative,
            audio_path=Path(audio_path) if audio_path else None,
            sections={},
            sources_used=sources,
            generated_at=datetime.now(),
            model_used=self._model,
            voice_used=self._voice_id,
            quality_score=quality_score,
            evaluator_feedback=evaluator_feedback,
        )

        store = DigestStore(db_path=self._digest_store_path)
        store.save(artifact)
        store.close()

        self._emit_turn_end(turns=1)
        return AgentResult(
            content=narrative,
            tool_results=(
                [collect_result]
                + [result for _, result in browser_results]
                + ([tts_result] if tts_result else [])
            ),
            turns=1,
            metadata={
                "audio_path": audio_path,
                "sources_used": sources,
            },
        )


def build_morning_digest_agent(
    engine: Any,
    model: str,
    config: Any,
    *,
    bus: Any = None,
    generate_audio: bool = True,
) -> Optional[MorningDigestAgent]:
    """Build a ready-to-run MorningDigestAgent from live config, or None.

    Shared factory for the chat-routing bridge in server/routes.py.
    Mirrors the kwargs-building logic in system/orchestrator.py's
    ``_run_agent`` (agent_name == "morning_digest" branch) — that class has
    its own separate copy, kept as-is rather than refactored to avoid risk
    to its own test suite for a change scoped to the web chat endpoint.
    """
    if not AgentRegistry.contains("morning_digest"):
        return None

    agent_kwargs: dict[str, Any] = {
        "bus": bus,
        "generate_audio": generate_audio,
    }
    dc = getattr(config, "digest", None)
    if dc is not None:
        section_sources: dict[str, Any] = {}
        for sec in dc.sections:
            sc = getattr(dc, sec, None)
            if sc and hasattr(sc, "sources"):
                section_sources[sec] = sc.sources
        agent_kwargs.update(
            {
                "persona": dc.persona,
                "sections": dc.sections,
                "section_sources": section_sources,
                "timezone": dc.timezone,
                "voice_id": dc.voice_id,
                "voice_speed": dc.voice_speed,
                "voice_volume": dc.voice_volume,
                "tts_backend": dc.tts_backend,
                "honorific": dc.honorific,
            }
        )

    from openjarvis.tools.digest_collect import DigestCollectTool

    tools = [DigestCollectTool()]
    # Outlook and Teams have no usable connector, so the digest reads them the
    # same way the user does. Imported defensively: both need the browser
    # tools' optional dependencies, and a missing one should cost those
    # sections, not the whole briefing.
    for module_name, class_name in (
        ("openjarvis.tools.opera_control", "OutlookReadTool"),
        ("openjarvis.tools.teams_read", "TeamsReadTool"),
    ):
        try:
            module = __import__(module_name, fromlist=[class_name])
            tools.append(getattr(module, class_name)())
        except Exception:  # pragma: no cover - optional dependency surface
            logger.warning("%s unavailable for the digest", class_name, exc_info=True)
    if generate_audio:
        from openjarvis.tools.text_to_speech import TextToSpeechTool

        tools.append(TextToSpeechTool())
    agent_kwargs["tools"] = tools

    return MorningDigestAgent(engine, model, **agent_kwargs)


_DIGEST_CRON_PROMPT = "Generate my morning digest."
_DIGEST_TASK_KEY = "digest-daily"
_DIGEST_TASK_KEY_FIELD = "openjarvis_task_key"


def register_digest_cron(scheduler: Any, *, cron_expr: str = "") -> Any:
    """Register the daily pre-generation of the briefing text.

    Pre-generating is the whole point: a briefing built on demand waits on
    Teams and both mailboxes, and the user asking at 7am should not pay for
    that. The scheduled run writes the text to ``digest.db``; the chat route
    serves it instantly and only rebuilds when asked for the *latest* one.

    Idempotent in the same way as the proactive cron, and for the same reason:
    this runs on every startup, and a paused task is an explicit user choice
    that must survive a restart.
    """
    from openjarvis.core.config import load_config

    if not cron_expr:
        try:
            cron_expr = load_config().digest.schedule
        except Exception:
            cron_expr = "0 21 * * *"

    metadata = {_DIGEST_TASK_KEY_FIELD: _DIGEST_TASK_KEY}
    existing = [
        task
        for task in scheduler.list_tasks()
        if task.status in {"active", "paused"}
        and task.agent == "morning_digest"
        and task.metadata.get(_DIGEST_TASK_KEY_FIELD) == _DIGEST_TASK_KEY
    ]

    paused = [task for task in existing if task.status == "paused"]
    if paused:
        keep = min(paused, key=lambda task: task.id)
        _cancel_digest_duplicates(scheduler, existing, keep=keep)
        return keep

    matching = [
        task
        for task in existing
        if task.schedule_type == "cron" and task.schedule_value == cron_expr
    ]
    if matching:
        keep = min(matching, key=lambda task: task.id)
        _cancel_digest_duplicates(scheduler, existing, keep=keep)
        return keep

    _cancel_digest_duplicates(scheduler, existing)
    return scheduler.create_task(
        prompt=_DIGEST_CRON_PROMPT,
        schedule_type="cron",
        schedule_value=cron_expr,
        agent="morning_digest",
        context_mode="isolated",
        metadata=metadata,
    )


def _cancel_digest_duplicates(
    scheduler: Any, tasks: List[Any], *, keep: Optional[Any] = None
) -> None:
    for task in tasks:
        if keep is not None and task.id == keep.id:
            continue
        try:
            scheduler.cancel_task(task.id)
        except Exception:
            logger.warning(
                "Failed to cancel duplicate digest task %s", task.id, exc_info=True
            )


#: ``digest_collect`` ends its output with a section listing every connector
#: that failed or is unconfigured.
_ERRORS_SECTION = "=== ERRORS ==="


def _without_errors(collected: str) -> str:
    """The collected evidence with the connector-failure section removed.

    The briefing kept ending on "two connectors failed: Google Tasks returned
    a 403, and Apple Health, Oura and Apple Music are unconnected" — true, and
    a waste of a 200-word budget the user hears out loud. It survived an
    explicit rule against naming unavailable sources, because the failures
    were sitting in the evidence and the model treated them as material.

    Removing them from the input settles it: there is nothing to narrate. The
    failures are logged instead, which is where an unconfigured connector
    actually belongs.
    """
    text = collected or ""
    head, marker, errors = text.partition(_ERRORS_SECTION)
    if not marker:
        return text
    trimmed = errors.strip()
    if trimmed:
        logger.info("digest sources unavailable: %s", " | ".join(trimmed.split("\n")))
    return head.rstrip()


#: Tool classes for the browser sources, resolved lazily.
_BROWSER_TOOL_CLASSES = {
    "outlook_read": ("openjarvis.tools.opera_control", "OutlookReadTool"),
    "teams_read": ("openjarvis.tools.teams_read", "TeamsReadTool"),
}


def _run_browser_tool(tool_name: str, count: int = 8) -> Any:
    """Run a browser source directly, without going through an executor.

    The agent instantiates these itself rather than relying on whoever built
    it to have passed them in. There are at least two construction sites —
    ``system/orchestrator.py`` and ``cli/ask.py`` — and wiring only the first
    produced a briefing that ran fine, collected Gmail and calendar, and
    silently answered "Unknown tool: teams_read", losing the assignment due
    the next day. A source the agent cannot function without is the agent's
    responsibility, not its caller's.
    """
    module_name, class_name = _BROWSER_TOOL_CLASSES[tool_name]
    module = __import__(module_name, fromlist=[class_name])
    return getattr(module, class_name)().execute(count=count)


#: Groups from a browser source whose items are dated commitments.
_DEADLINE_GROUPS = ("assignments",)


def _deadline_preamble(deadlines: str, narrative: str) -> str:
    """A plain statement of any deadline the narrative left out.

    The model was told three different ways to lead with these — in the
    section prompt, in the absolute rules, and as a REQUIRED line outside the
    evidence block. It dropped the assignment due the next day every time, and
    on the last attempt opened with "no urgent deadlines", which is worse than
    silence because it is confidently wrong.

    So the guarantee is made in code instead. A briefing may be imperfect
    prose; it may not lose a due date.
    """
    lowered = (narrative or "").casefold()
    missing = []
    for line in (deadlines or "").splitlines():
        item = line.lstrip("- ").strip()
        if not item:
            continue
        # The title runs up to the first "Due"/"at"; enough to tell whether
        # the narrative already covered it without matching on boilerplate.
        subject = re.split(r"\bdue\b", item, flags=re.IGNORECASE)[0]
        words = [word for word in re.findall(r"[A-Za-z]{4,}", subject)]
        if words and all(word.casefold() in lowered for word in words[-3:]):
            continue
        missing.append(item)
    if not missing:
        return ""
    if len(missing) == 1:
        return f"First, a deadline: {missing[0]}."
    listed = " ".join(f"{index}. {item}." for index, item in enumerate(missing, 1))
    return f"First, your deadlines: {listed}"


def _deadline_lines(browser_results: List[tuple]) -> str:
    """Dated commitments pulled out of the browser sources, one per line.

    Read from tool metadata rather than by parsing the rendered prose: the
    tool already knows which lines are assignments, and re-deriving that from
    its own output is a second, worse copy of the same knowledge.
    """
    lines = []
    for _, result in browser_results:
        items = (result.metadata or {}).get("items") or {}
        for label, entries in items.items():
            if str(label).strip().casefold() not in _DEADLINE_GROUPS:
                continue
            lines.extend(f"- {' '.join(str(entry).split())}" for entry in entries)
    return "\n".join(lines)
