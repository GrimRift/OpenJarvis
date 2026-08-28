"""Morning Digest Agent — synthesizes a daily briefing from multiple sources.

Thin orchestrator that delegates to digest_collect (data fetching),
the LLM (narrative synthesis), and text_to_speech (audio generation).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from openjarvis.agents._stubs import AgentContext, AgentResult, ToolUsingAgent
from openjarvis.agents.digest_store import DigestArtifact, DigestStore
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
}


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
        self._tts_backend = kwargs.pop("tts_backend", "cartesia")
        self._generate_audio = bool(kwargs.pop("generate_audio", True))
        self._digest_store_path = kwargs.pop("digest_store_path", "")
        self._honorific = kwargs.pop("honorific", "sir")
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
            "- NEVER mention disconnected or unavailable sources.\n"
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
        collected_data = collect_result.content

        # Step 2: Synthesize narrative via LLM
        system_prompt = self._build_system_prompt()
        messages = [
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(
                role=Role.USER,
                content=(
                    "The following collected data is the only factual evidence for "
                    f"the briefing:\n\n<collected_data>\n{collected_data}\n"
                    "</collected_data>\n\nUse configured sections only. Omit missing "
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
        # Strip any markdown that slipped through (##, *, -, etc.)
        import re

        tts_text = re.sub(r"^#{1,6}\s+", "", narrative, flags=re.MULTILINE)
        tts_text = re.sub(r"^\s*[-*•]\s+", "", tts_text, flags=re.MULTILINE)
        tts_text = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", tts_text)
        tts_text = tts_text.strip()

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
                        "output_dir": output_dir,
                    }
                ),
            )
            tts_result = self._executor.execute(tts_call)
            audio_path = (
                tts_result.metadata.get("audio_path", "")
                if tts_result.success
                else ""
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
            tool_results=[collect_result] + ([tts_result] if tts_result else []),
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
                "tts_backend": dc.tts_backend,
                "honorific": dc.honorific,
            }
        )

    from openjarvis.tools.digest_collect import DigestCollectTool
    tools = [DigestCollectTool()]
    if generate_audio:
        from openjarvis.tools.text_to_speech import TextToSpeechTool

        tools.append(TextToSpeechTool())
    agent_kwargs["tools"] = tools

    return MorningDigestAgent(engine, model, **agent_kwargs)
