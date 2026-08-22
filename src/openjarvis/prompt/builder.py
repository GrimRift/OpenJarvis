from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional, Tuple

from openjarvis.core.config import MemoryFilesConfig, SystemPromptConfig
from openjarvis.core.paths import get_config_dir

PromptCacheSegment = Literal["frozen_prefix", "dynamic_suffix"]


@dataclass(frozen=True, slots=True)
class PromptSection:
    """Inspectable prompt section emitted by SystemPromptBuilder."""

    name: str
    content: str
    source: str
    cache_segment: PromptCacheSegment


class SystemPromptBuilder:
    """Assembles system prompts with frozen prefix for cache stability."""

    def __init__(
        self,
        agent_template: str,
        memory_files_config: Optional[MemoryFilesConfig] = None,
        system_prompt_config: Optional[SystemPromptConfig] = None,
        skill_index: Optional[List[Tuple[str, str]]] = None,
        session_context: Optional[str] = None,
        previous_state: Optional[str] = None,
        skill_catalog_xml: Optional[str] = None,
        skill_few_shot: Optional[List[str]] = None,
        skill_few_shot_examples: Optional[List[str]] = None,
    ) -> None:
        self._agent_template = agent_template
        _mf = memory_files_config or MemoryFilesConfig()
        self._mf_config = self._resolve_persona(_mf)
        self._sp_config = system_prompt_config or SystemPromptConfig()
        self._skill_index = skill_index or []
        self._session_context = session_context
        self._previous_state = previous_state
        self._skill_catalog_xml = skill_catalog_xml
        # Allow either name; skill_few_shot_examples is the Plan 2A canonical name.
        if skill_few_shot_examples is not None:
            self._skill_few_shot = list(skill_few_shot_examples)
        else:
            self._skill_few_shot = list(skill_few_shot or [])
        self._frozen_prefix: Optional[str] = None
        self._frozen_sections: Optional[list[PromptSection]] = None

    def build(self) -> str:
        # Derived from sections() rather than assembled separately, so the
        # two can never drift out of sync with each other (see
        # test_sections_expose_prompt_metadata's invariant).
        return "\n\n".join(section.content for section in self.sections())

    @staticmethod
    def _current_datetime_content() -> str:
        # Computed fresh on every call, deliberately kept out of the cached
        # frozen prefix — the LLM has no real clock, so without this it
        # guesses at dates/weekdays (and gets them wrong). Caching it would
        # go stale for the lifetime of this builder instance.
        #
        # The trailing sentence exists because the date alone wasn't enough
        # in practice: a small local model, when reading a schedule note
        # whose rows are labeled by recurring weekday (e.g. "Day: Friday"),
        # conflated that per-row label with "today" and answered a general
        # schedule question as if today were Friday when this field said
        # Saturday — actively contradicting its own system prompt. The two
        # concepts needed to be named as distinct, not just juxtaposed.
        now = datetime.now()
        return (
            f"## Current Date and Time\n\n{now.strftime('%A, %B %d, %Y, %I:%M %p')}\n\n"
            "This is today's real date. When a note or tool result lists a "
            "recurring weekday (e.g. a class schedule's \"Day\" column), that "
            "is which weekday the entry happens on, not a claim about today — "
            "compare it against the date above before saying something is "
            "\"today\" or happening \"soon.\""
        )

    def sections(self) -> list[PromptSection]:
        """Return prompt sections with lightweight cache/debug metadata."""
        sections = [
            *self._get_frozen_sections(),
            PromptSection(
                name="current_datetime",
                content=self._current_datetime_content(),
                source="current_datetime",
                cache_segment="dynamic_suffix",
            ),
        ]
        if self._session_context:
            sections.append(
                PromptSection(
                    name="session_context",
                    content=f"## Session Context\n\n{self._session_context}",
                    source="session_context",
                    cache_segment="dynamic_suffix",
                )
            )
        if self._previous_state:
            sections.append(
                PromptSection(
                    name="previous_state",
                    content=f"## Previous State\n\n{self._previous_state}",
                    source="previous_state",
                    cache_segment="dynamic_suffix",
                )
            )
        # Repeated at the very end, not just after the frozen prefix: with a
        # large persona/skills/retrieved-context prefix (seen in practice at
        # 45k+ input tokens for a single query), the first mention can be far
        # enough back in a long prompt that a small model effectively loses
        # it. Models attend most reliably to the end of a long context,
        # right before the actual question — repeating it there costs a
        # dozen tokens and measurably improves the odds it's actually used.
        now = datetime.now()
        sections.append(
            PromptSection(
                name="datetime_reminder",
                content=f"(Reminder: today is really {now.strftime('%A, %B %d, %Y')}.)",
                source="datetime_reminder",
                cache_segment="dynamic_suffix",
            )
        )
        # Placed last for the same reason as the date reminder above, and
        # aimed at a specific observed failure: asked to open an app a
        # second time, with the earlier "<app> has been opened for you" still
        # in the conversation, the model reproduced that sentence verbatim
        # and called nothing — 9 completion tokens, no tool call, nothing
        # opened. Prior turns read as a template to copy rather than as
        # history, so the instruction has to say that acting is required
        # *again*, not merely that tools exist.
        sections.append(
            PromptSection(
                name="tool_use_reminder",
                content=(
                    "(Reminder: to do something in the world — open an app, "
                    "play music, send a notification, write a file — you must "
                    "call the tool for it. A similar request earlier in this "
                    "conversation does not count: repeat the tool call. Never "
                    "say an action is done unless you called its tool in this "
                    "turn and it succeeded.)"
                ),
                source="tool_use_reminder",
                cache_segment="dynamic_suffix",
            )
        )
        return sections

    def _get_frozen_sections(self) -> list[PromptSection]:
        if self._frozen_sections is None:
            self._frozen_sections = self._build_frozen_sections()
        return self._frozen_sections

    def _persona_sections(self) -> list[str]:
        """The SOUL / MEMORY / USER sections (no agent template, no skills)."""
        sections: list[str] = []
        soul = self._load_file(
            self._mf_config.soul_path,
            self._sp_config.soul_max_chars,
        )
        if soul:
            sections.append(f"## Agent Persona\n\n{soul}")
        memory = self._load_file(
            self._mf_config.memory_path,
            self._sp_config.memory_max_chars,
        )
        if memory:
            sections.append(f"## Agent Memory\n\n{memory}")
        user = self._load_file(
            self._mf_config.user_path,
            self._sp_config.user_max_chars,
        )
        if user:
            sections.append(f"## User Profile\n\n{user}")
        return sections

    def persona_sections(self) -> str:
        """Just the SOUL / MEMORY / USER persona, joined.

        For agents that assemble their own system prompt (monitor_operative,
        operative) and want to *append* persona without letting the builder
        replace their specialized instructions (#376). Returns "" when no
        persona files are present.
        """
        return "\n\n".join(self._persona_sections())

    def _build_frozen_prefix(self) -> str:
        return "\n\n".join(section.content for section in self._get_frozen_sections())

    def _build_frozen_sections(self) -> list[PromptSection]:
        sections: list[PromptSection] = []
        # Config-driven persona prefix from [system_prompt] prefix (#401),
        # prepended ahead of the agent template so it leads the frozen prefix.
        if self._sp_config.prefix:
            sections.append(
                PromptSection(
                    name="prefix",
                    content=self._sp_config.prefix,
                    source="system_prompt.prefix",
                    cache_segment="frozen_prefix",
                )
            )
        if self._agent_template:
            sections.append(
                PromptSection(
                    name="agent_template",
                    content=self._agent_template,
                    source="agent_template",
                    cache_segment="frozen_prefix",
                )
            )
        sections.extend(self._persona_prompt_sections())
        # XML skill catalog (preferred over legacy markdown list)
        if self._skill_catalog_xml:
            sections.append(
                PromptSection(
                    name="skill_catalog",
                    content="## Available Skills\n\n" + self._skill_catalog_xml,
                    source="skill_catalog_xml",
                    cache_segment="frozen_prefix",
                )
            )
        elif self._skill_index:
            skill_lines = []
            for name, desc in self._skill_index:
                truncated = desc[: self._sp_config.skill_desc_max_chars]
                if len(desc) > self._sp_config.skill_desc_max_chars:
                    truncated = truncated[:-3] + "..."
                skill_lines.append(f"- **{name}**: {truncated}")
            sections.append(
                PromptSection(
                    name="skill_index",
                    content="## Available Skills\n\n" + "\n".join(skill_lines),
                    source="skill_index",
                    cache_segment="frozen_prefix",
                )
            )
        if self._skill_few_shot:
            examples = "\n\n".join(self._skill_few_shot)
            sections.append(
                PromptSection(
                    name="skill_examples",
                    content="## Skill Examples\n\n" + examples,
                    source="skill_few_shot_examples",
                    cache_segment="frozen_prefix",
                )
            )
        return sections

    def _persona_prompt_sections(self) -> list[PromptSection]:
        sections: list[PromptSection] = []
        self._append_file_section(
            sections=sections,
            name="soul",
            heading="Agent Persona",
            path_str=self._mf_config.soul_path,
            max_chars=self._sp_config.soul_max_chars,
        )
        self._append_file_section(
            sections=sections,
            name="memory",
            heading="Agent Memory",
            path_str=self._mf_config.memory_path,
            max_chars=self._sp_config.memory_max_chars,
        )
        self._append_file_section(
            sections=sections,
            name="user",
            heading="User Profile",
            path_str=self._mf_config.user_path,
            max_chars=self._sp_config.user_max_chars,
        )
        return sections

    def _append_file_section(
        self,
        sections: list[PromptSection],
        name: str,
        heading: str,
        path_str: str,
        max_chars: int,
    ) -> None:
        content = self._load_file(path_str, max_chars)
        if content:
            sections.append(
                PromptSection(
                    name=name,
                    content=f"## {heading}\n\n{content}",
                    source=str(Path(path_str).expanduser()),
                    cache_segment="frozen_prefix",
                )
            )

    def _load_file(self, path_str: str, max_chars: int) -> str:
        # An empty path means "no file" (e.g. the persona "none" opt-out, which
        # resolves to empty paths). Guard before Path("") — which becomes "." —
        # so reading it does not raise IsADirectoryError.
        if not path_str:
            return ""
        path = Path(path_str).expanduser()
        if not path.exists():
            return ""
        # Always read as UTF-8. On Windows, ``read_text()`` falls back to the
        # system code page (e.g. cp950 for zh-TW, cp932 for ja) and raises
        # ``UnicodeDecodeError`` on any non-ASCII persona content.
        content = path.read_text(encoding="utf-8")
        if len(content) <= max_chars:
            return content
        return self._truncate(content, max_chars)

    def _truncate(self, text: str, max_chars: int) -> str:
        if self._sp_config.truncation_strategy == "head_tail":
            head_size = int(max_chars * 0.7)
            tail_size = int(max_chars * 0.2)
            omitted = len(text) - head_size - tail_size
            return (
                text[:head_size]
                + f"\n\n[...truncated {omitted} chars...]\n\n"
                + text[-tail_size:]
            )
        return text[:max_chars] + "\n[...truncated...]"

    @staticmethod
    def _resolve_persona(mf: MemoryFilesConfig) -> MemoryFilesConfig:
        """Resolve persona_name to effective file paths.
        - "" (empty) -> use mf's existing paths (global default, unchanged)
        - "none"      -> empty paths (opt-out, no persona injected)
        - "<name>"    -> ~/.openjarvis/personas/<name>/{SOUL,MEMORY,USER}.md
        """
        if not mf.persona_name:
            return mf
        if mf.persona_name == "none":
            return MemoryFilesConfig(
                soul_path="",
                memory_path="",
                user_path="",
                nudge_interval=mf.nudge_interval,
            )
        name = mf.persona_name
        if ".." in name or "/" in name or "\\" in name or name.startswith("/"):
            raise ValueError(
                f"Invalid persona name {name!r}: must be a simple "
                "identifier (no path separators or '..')."
            )
        base = get_config_dir() / "personas" / name
        return MemoryFilesConfig(
            soul_path=str(base / "SOUL.md"),
            memory_path=str(base / "MEMORY.md"),
            user_path=str(base / "USER.md"),
            nudge_interval=mf.nudge_interval,
            persona_name=name,
        )
