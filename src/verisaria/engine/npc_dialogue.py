"""NPC Dialogue Generator (P1.1).

Generates an NPC's spoken line via the LLM, grounded ONLY in that NPC's own
cognition — persona/traits, its own memories, the observable conversation so
far, and the world-book knowledge THIS NPC's scope grants (filtered through
WorldBookFilter, so a refugee can surface the forbidden massacre while the
watch never even sees it). This keeps the NPC free of a god's-eye view of the
world (architecture red line A5): the prompt never contains canonical world
truth the NPC has no access to, nor other actors' private knowledge.

The LLM here only proposes *narrative content* (what the NPC says). It does not
touch world state — the line becomes the `content` of a speech Action, which
flows through the normal Event pipeline like any template line would.

Determinism / replay: when the LLM is unavailable (FakeLLM with no matching
fixture, Ollama down, budget exceeded, validation failure) `generate_line`
returns ``None`` and the caller falls back to deterministic templates. Tests and
``--fake`` replays therefore stay reproducible; real Ollama play gets lively,
memory-driven dialogue.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from pydantic import BaseModel, ValidationError

from verisaria.engine.intent import PromptLoader
from verisaria.engine.llm import LLMCallRequest, LLMOrchestrator
from verisaria.engine.world_book_filter import WorldBookFilter


class NPCDialogue(BaseModel):
    """LLM-proposed dialogue line for an NPC (content only, never world state)."""

    line: str
    tone: str | None = None


class NPCDialogueGenerator:
    """Produce a single in-character NPC line through the LLM, or ``None``."""

    TASK_TYPE = "generate_npc_dialogue"

    def __init__(
        self,
        llm_orchestrator: LLMOrchestrator,
        prompt_loader: PromptLoader | None = None,
        max_memories: int = 3,
        temperature: float = 0.8,
        world_book: list[Any] | None = None,
    ) -> None:
        self.llm = llm_orchestrator
        self.prompts = prompt_loader or PromptLoader()
        self.max_memories = max_memories
        self.temperature = temperature
        # Static pack world-book; filtered per-NPC at prompt time so an NPC can
        # reference/reveal only what their own scope grants (A5 layered truth).
        self.world_book = world_book or []
        # Per-NPC ring buffer of recently *spoken* lines, fed back into the prompt
        # so an NPC varies its phrasing instead of repeating itself across ticks.
        self._recent_lines: dict[str, deque[str]] = {}
        self._recent_keep = 3

    def note_spoken(self, npc_id: str, line: str) -> None:
        """Record a line the NPC actually said, so future prompts can steer it away
        from repeating itself (real providers only; FakeLLM/template paths never
        call this, keeping replays deterministic)."""
        line = (line or "").strip()
        if not line:
            return
        buf = self._recent_lines.get(npc_id)
        if buf is None:
            buf = deque(maxlen=self._recent_keep)
            self._recent_lines[npc_id] = buf
        buf.append(line)

    def _recent_section(self, npc_id: str) -> str:
        """A ready-to-embed prompt section (or '') listing the NPC's recent lines."""
        buf = self._recent_lines.get(npc_id)
        if not buf:
            return ""
        lines = "\n".join(f"- {ln}" for ln in buf)
        return (
            "## 你最近已经说过的话（别再重复，换个角度或说法）\n"
            f"{lines}\n\n"
        )

    def generate_line(
        self,
        npc_id: str,
        entity: Any | None = None,
        world: Any | None = None,
        memory_store: Any | None = None,
        conversation_session: Any | None = None,
        directive: str | None = None,
    ) -> str | None:
        """Return an LLM-generated line, or ``None`` to let the caller fall back.
        ``directive`` injects a current-situation steer (e.g. the arbiter verdict the
        NPC must voice), so the reply reflects what just happened — not generic
        chatter — even on the non-streaming path."""
        try:
            prompt = self._build_prompt(
                npc_id, entity, memory_store, conversation_session, world=world,
                directive=directive,
            )
            result = self.llm.call(
                LLMCallRequest(
                    task_type=self.TASK_TYPE,
                    prompt=prompt,
                    schema_model=NPCDialogue,
                    model_preference="ollama",
                    temperature=self.temperature,
                )
            )
            if not result.success or not result.data:
                return None
            dialogue = NPCDialogue.model_validate(result.data)
        except (ValidationError, Exception):
            # Never let dialogue generation crash a tick — degrade to templates.
            return None
        line = (dialogue.line or "").strip()
        return line or None

    def generate_line_stream(
        self,
        npc_id: str,
        entity: Any | None = None,
        world: Any | None = None,
        memory_store: Any | None = None,
        conversation_session: Any | None = None,
        on_delta=None,
        directive: str | None = None,
    ) -> str | None:
        """Stream a PLAIN-TEXT line token-by-token (firing ``on_delta``) so the UI
        can render an NPC reply as it generates. Returns the full line, or ``None``
        when streaming is unavailable / fails so the caller falls back."""
        provider = getattr(self.llm, "primary", None)
        if provider is None or not getattr(provider, "supports_streaming", False):
            return None
        try:
            prompt = self._build_prompt_plain(
                npc_id, entity, memory_store, conversation_session, directive, world=world
            )
            result = provider.call_stream(
                LLMCallRequest(
                    task_type=self.TASK_TYPE, prompt=prompt,
                    model_preference="ollama", temperature=self.temperature,
                ),
                on_delta=on_delta,
            )
        except Exception:
            return None
        if not result.success:
            return None
        line = (result.raw_response or "").strip().strip("「」\"' ")
        return line or None

    # -- prompt construction (NPC's own cognition only — A5) --

    def _build_prompt_plain(
        self,
        npc_id: str,
        entity: Any | None,
        memory_store: Any | None,
        session: Any | None,
        directive: str | None = None,
        world: Any | None = None,
    ) -> str:
        """Like ``_build_prompt`` but asks for a bare line (no JSON) so the output
        can be streamed straight to the player. ``directive`` injects a current
        situation the NPC is responding to (e.g. a decision they just made)."""
        try:
            template = self.prompts.load(self.TASK_TYPE, "v1.0.0")
        except (FileNotFoundError, Exception):
            template = self._fallback_prompt()
        persona = self._persona_block(npc_id, entity)
        environment = self._environment_section(entity, world)
        memories = self._memory_block(
            npc_id, memory_store, query=self._memory_query(session, directive)
        )
        knowledge = self._knowledge_section(entity)
        recent = self._recent_section(npc_id)
        conversation = self._conversation_block(npc_id, session)
        situation = f"## 当前情境\n{directive}\n\n" if directive else ""
        return (
            f"{template}\n\n"
            f"## 你的角色\n{persona}\n\n"
            f"{environment}"
            f"## 你记得的事（只有你自己知道的）\n{memories}\n\n"
            f"{knowledge}"
            f"{recent}"
            f"## 当前对话\n{conversation}\n\n"
            f"{situation}"
            "## 输出\n"
            "直接输出一句符合角色身份的中文台词本身。不要 JSON、不要花括号、"
            "不要 markdown 代码块（```）、不要引号、不要解释，只要那一句话。"
        )

    def _build_prompt(
        self,
        npc_id: str,
        entity: Any | None,
        memory_store: Any | None,
        session: Any | None,
        world: Any | None = None,
        directive: str | None = None,
    ) -> str:
        try:
            template = self.prompts.load(self.TASK_TYPE, "v1.0.0")
        except FileNotFoundError:
            template = self._fallback_prompt()

        persona = self._persona_block(npc_id, entity)
        environment = self._environment_section(entity, world)
        memories = self._memory_block(
            npc_id, memory_store, query=self._memory_query(session, directive)
        )
        knowledge = self._knowledge_section(entity)
        recent = self._recent_section(npc_id)
        conversation = self._conversation_block(npc_id, session)
        situation = f"## 当前情境\n{directive}\n\n" if directive else ""

        return (
            f"{template}\n\n"
            f"## 你的角色\n{persona}\n\n"
            f"{environment}"
            f"## 你记得的事（只有你自己知道的）\n{memories}\n\n"
            f"{knowledge}"
            f"{recent}"
            f"{situation}"
            f"## 当前对话\n{conversation}\n\n"
            "## 输出\n"
            '只返回 JSON：{"line": "<一句符合角色身份的中文台词>"}'
        )

    def _persona_block(self, npc_id: str, entity: Any | None) -> str:
        name = npc_id.replace("npc.", "")
        parts = [f"你是 {name}。"]
        traits = list(getattr(entity, "traits", []) or []) if entity is not None else []
        if traits:
            parts.append("性格特征：" + "、".join(str(t) for t in traits) + "。")
        return " ".join(parts)

    @staticmethod
    def _keyword_set(text: str) -> set[str]:
        """Lightweight lexical tokens (char-bigrams + words) for CN/EN overlap."""
        if not text:
            return set()
        seps = "，。；、！？,.;!?　 \t\n\"'「」『』（）()…—-~·"
        out: set[str] = set()
        cleaned = "".join(" " if c in seps else c for c in text)
        for w in cleaned.split():
            if len(w) >= 2:
                out.add(w)
        for i in range(len(text) - 1):
            bg = text[i : i + 2]
            if not any(c in seps for c in bg):
                out.add(bg)
        return out

    def _memory_block(
        self, npc_id: str, memory_store: Any | None, query: str | None = None
    ) -> str:
        """The NPC's most pertinent memories. Ranked by salience ALONE the most
        *important* memories crowded out the *relevant* ones — a refugee asked about
        the massacre got back survival worries (the live drift). When a ``query``
        (what the player just said / the active topic) is given, rank by
        salience × (1 + lexical relevance) so on-topic memories surface."""
        if memory_store is None:
            return "（没有特别的记忆）"
        try:
            memories = list(memory_store.get_all(npc_id))
        except Exception:
            return "（没有特别的记忆）"
        if not memories:
            return "（没有特别的记忆）"
        q = self._keyword_set(query) if query else set()
        if q:
            def _score(m: Any) -> float:
                sal = getattr(m, "salience", 0.0) or 0.0
                rel = len(self._keyword_set(getattr(m, "content", "")) & q)
                return sal * (1.0 + rel)
            memories.sort(key=_score, reverse=True)
        else:
            memories.sort(key=lambda m: getattr(m, "salience", 0.0), reverse=True)
        lines = [f"- {getattr(m, 'content', '')}" for m in memories[: self.max_memories]]
        return "\n".join(lines)

    @staticmethod
    def _memory_query(session: Any | None, directive: str | None = None) -> str:
        """What the NPC is currently being asked / talking about — used to retrieve
        the memories most relevant to *this* moment, not just the most salient ones."""
        parts: list[str] = []
        if session is not None:
            shared = getattr(session, "shared_context", {}) or {}
            last = shared.get("last_content")
            if last:
                parts.append(str(last))
            parts.extend(str(t) for t in (getattr(session, "topic_stack", []) or []))
        if directive:
            parts.append(str(directive))
        return " ".join(parts)

    # How each world-book layer should COLOUR what the NPC believes it to be —
    # a confirmed fact, doctrine they were taught, a buried secret, hearsay.
    _LAYER_FRAMING: dict[str, str] = {
        "canonical_fact": "你确知",
        "personal_truth": "你确知",
        "forbidden_knowledge": "你私下晓得、但无人敢公开提的隐情",
        "faction_propaganda": "你被教导要相信",
        "public_belief": "大家都这么说",
        "local_rumor": "你听人传过",
    }

    def _knowledge_block(self, entity: Any | None) -> str:
        """Render the world-book knowledge THIS NPC's scope grants, framed by layer
        (a confirmed fact vs. taught doctrine vs. a buried secret). Empty when the
        NPC has no accessible entries. A5: filtered per-entity, so an NPC literally
        cannot reference what they have no access to."""
        if not self.world_book or entity is None:
            return ""
        try:
            entries = WorldBookFilter.filter_for_entity(self.world_book, entity)
        except Exception:
            return ""
        if not entries:
            return ""
        lines = []
        for e in entries:
            layer = getattr(e, "layer", "") or ""
            frame = self._LAYER_FRAMING.get(layer, "你知道")
            lines.append(f"- （{frame}）{getattr(e, 'content', '')}")
        return "\n".join(lines)

    def _knowledge_section(self, entity: Any | None) -> str:
        """A ready-to-embed prompt section (or '') for the NPC's accessible lore."""
        block = self._knowledge_block(entity)
        if not block:
            return ""
        return (
            "## 你心里清楚的事（世道、传闻与隐情）\n"
            f"{block}\n"
            "（若对方正问到上面这些事，就**正面回应那句话**——你可以坦白、含糊其辞、"
            "守口如瓶或假装不知，但要让人看出你是在回应这个问题，别扯到无关的话头上。"
            "说不说、怎么说，由你的性格和对对方的信任决定；但绝不能说出上面没有、"
            "你这个角色不可能知道的事。）\n\n"
        )

    @staticmethod
    def _present_name(other: Any) -> str:
        """How a co-located entity is referred to in an NPC's situational view."""
        if getattr(other, "entity_type", None) == "player":
            return "一个外来者"
        return str(getattr(other, "entity_id", "")).replace("npc.", "")

    def _environment_section(self, entity: Any | None, world: Any | None) -> str:
        """The NPC's PERCEIVABLE surroundings — where they are and who is co-located
        (A5: only same-location entities; never anyone elsewhere). Grounds the reply
        in the here-and-now so it doesn't drift to whatever memory is most salient."""
        if entity is None or world is None:
            return ""
        loc = getattr(entity, "location_id", None)
        entities = getattr(getattr(world, "state", world), "entities", None)
        if not loc or not entities:
            return ""
        try:
            others = [
                e for e in entities.values()
                if getattr(e, "location_id", None) == loc
                and getattr(e, "entity_id", None) != getattr(entity, "entity_id", None)
            ]
        except Exception:
            return ""
        parts = [f"你此刻在「{loc}」。"]
        if others:
            names = "、".join(self._present_name(o) for o in others[:8])
            parts.append(f"在你身边的有：{names}。")
        else:
            parts.append("此处眼下只有你一人。")
        return "## 你此刻的处境\n" + "".join(parts) + "\n\n"

    def _conversation_block(self, npc_id: str, session: Any | None) -> str:
        if session is None:
            return "（当前不在对话中，随口说一句即可）"
        parts: list[str] = []
        topics = list(getattr(session, "topic_stack", []) or [])
        if topics:
            parts.append("已聊到的话题：" + "、".join(str(t) for t in topics) + "。")
        shared = getattr(session, "shared_context", {}) or {}
        last_speaker = shared.get("last_speaker")
        last_content = shared.get("last_content")
        if last_speaker and last_speaker != npc_id and last_content:
            parts.append(f'对方刚才说："{last_content}"')
        return "\n".join(parts) if parts else "（对话刚开始）"

    @staticmethod
    def _fallback_prompt() -> str:
        return (
            "你是一个 RPG 世界里的 NPC。根据下面提供的你的角色设定、你自己的记忆和"
            "当前对话，说出一句符合身份、简短自然的台词。只能使用你自己知道的信息，"
            "不要编造世界设定或别人的秘密。"
        )
