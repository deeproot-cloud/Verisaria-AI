"""PLAY-3 Channel A — relationship appraisal.

When an NPC perceives a socially-weighted player action, the *appraiser* asks the
LLM (through the provider seam, so FakeLLM/replay stays deterministic) how that
action changes the NPC's stance toward the actor. The LLM returns, in the NPC's
own voice/knowledge (A5 — NPC cognition only, never world truth):

  - ``belief``: a natural-language claim the NPC now holds (fed to memory so it
    is *remembered* and *explainable*, and surfaces in later dialogue);
  - ``deltas``: bounded per-dimension changes to the relationship snapshot;
  - ``reason``: a short rationale (telemetry / narration hint).

The numeric consequence is delta-driven and cumulative (``RelationshipStore``),
not derived from brittle keyword matching — that keeps the magnitude under the
engine's control (two clamps: per-appraisal and per-dimension unit range) while
the *direction/nuance* comes from the model.

Determinism / replay: the appraisal call goes through ``LLMOrchestrator``; under
FakeLLM it returns a fixture, so tests and ``replay`` are reproducible. When the
LLM is unavailable or its output fails validation, ``appraise`` returns ``None``
and the caller simply records no change (P5 degradation — never crashes a tick).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from verisaria.engine.intent import PromptLoader
from verisaria.engine.llm import LLMCallRequest, LLMOrchestrator
from verisaria.engine.memory import RelationshipCalculator
from verisaria.engine.schemas import RelationshipSnapshot

DIMENSIONS = RelationshipCalculator.DIMENSIONS


# ---------------------------------------------------------------------------
# Appraisal output schema (NPC cognition only)
# ---------------------------------------------------------------------------

class AppraisalResult(BaseModel):
    """LLM-proposed change to one NPC's stance toward an actor."""

    belief: str
    deltas: dict[str, float] = Field(default_factory=dict)
    reason: str | None = None


# ---------------------------------------------------------------------------
# Relationship store (cumulative, delta-driven, toward any target)
# ---------------------------------------------------------------------------

class RelationshipStore:
    """Cumulative relationship dimensions per (npc_id, target_id).

    Distinct from ``RelationshipCalculator`` (which derives a snapshot from an
    NPC's beliefs and is used for NPC↔NPC familiarity gating): this store holds
    the *appraisal-accumulated* stance, clamped to the unit range, and is what
    the player can query / what makes choices have consequence.
    """

    def __init__(self) -> None:
        self._dims: dict[tuple[str, str], dict[str, float]] = {}
        self._tick: dict[tuple[str, str], int] = {}

    def apply(
        self,
        npc_id: str,
        target_id: str,
        deltas: dict[str, float],
        tick: int,
    ) -> None:
        key = (npc_id, target_id)
        dims = self._dims.setdefault(key, {d: 0.0 for d in DIMENSIONS})
        for dim, delta in deltas.items():
            if dim not in dims:
                continue
            cur = dims[dim]
            # Diminishing returns toward the bound: a positive delta is scaled by
            # the remaining headroom (1 - cur), a negative one by how far there is
            # to fall (cur). So a stance moves fast while neutral and ever-slower
            # as it strengthens — it approaches 0/1 asymptotically instead of
            # slamming into the ceiling after a handful of appraisals.
            if delta >= 0:
                cur = cur + delta * (1.0 - cur)
            else:
                cur = cur + delta * cur
            dims[dim] = max(0.0, min(1.0, cur))
        self._tick[key] = tick

    def get(self, npc_id: str, target_id: str) -> RelationshipSnapshot | None:
        key = (npc_id, target_id)
        dims = self._dims.get(key)
        if dims is None:
            return None
        tick = self._tick.get(key, 0)
        dominant = [
            {"dimension": d, "value": v}
            for d, v in sorted(dims.items(), key=lambda x: x[1], reverse=True)
            if v > 0
        ][:3]
        return RelationshipSnapshot(
            snapshot_id=f"rel_{npc_id}_{target_id}_{tick}",
            npc_id=npc_id,
            target_id=target_id,
            tick=tick,
            dimensions=dict(dims),
            dominant_beliefs=dominant,
            updated_at_tick=tick,
        )

    def relationships_toward(self, target_id: str) -> list[RelationshipSnapshot]:
        """Every NPC's stance toward ``target_id`` (for player-facing display),
        strongest-relationship first."""
        snaps = [
            snap
            for (npc_id, t), _ in self._dims.items()
            if t == target_id
            for snap in [self.get(npc_id, target_id)]
            if snap is not None
        ]
        snaps.sort(
            key=lambda s: max(s.dimensions.values()) if s.dimensions else 0.0,
            reverse=True,
        )
        return snaps

    # -- persistence --

    def get_state(self) -> dict[str, Any]:
        return {
            "dims": {f"{k[0]}|{k[1]}": v for k, v in self._dims.items()},
            "tick": {f"{k[0]}|{k[1]}": v for k, v in self._tick.items()},
        }

    def load_state(self, state: dict[str, Any]) -> None:
        self._dims = {}
        self._tick = {}
        for k, v in state.get("dims", {}).items():
            npc_id, _, target_id = k.partition("|")
            self._dims[(npc_id, target_id)] = dict(v)
        for k, v in state.get("tick", {}).items():
            npc_id, _, target_id = k.partition("|")
            self._tick[(npc_id, target_id)] = v


# ---------------------------------------------------------------------------
# Appraiser
# ---------------------------------------------------------------------------

class RelationshipAppraiser:
    """Produce an :class:`AppraisalResult` for how a perceived player action
    changes an NPC's stance, or ``None`` to record no change (degrade)."""

    TASK_TYPE = "appraise_relationship"

    def __init__(
        self,
        llm_orchestrator: LLMOrchestrator,
        prompt_loader: PromptLoader | None = None,
        max_delta: float = 0.3,
        temperature: float = 0.5,
    ) -> None:
        self.llm = llm_orchestrator
        self.prompts = prompt_loader or PromptLoader()
        self.max_delta = max_delta
        self.temperature = temperature

    def appraise(
        self,
        npc_id: str,
        entity: Any | None,
        event: Any,
        world: Any | None = None,
        memory_store: Any | None = None,
        known_facts: list[str] | None = None,
        leverage: list[str] | None = None,
    ) -> AppraisalResult | None:
        """Return a clamped appraisal, or ``None`` to record no change.

        ``known_facts`` is what the NPC actually knows/believes (its accessible
        world-book), so the stance is grounded in the NPC's own worldview (A5)
        rather than its trait labels alone.
        """
        try:
            prompt = self._build_prompt(npc_id, entity, event, known_facts, memory_store,
                                        leverage=leverage)
            result = self.llm.call(
                LLMCallRequest(
                    task_type=self.TASK_TYPE,
                    prompt=prompt,
                    schema_model=AppraisalResult,
                    model_preference="ollama",
                    temperature=self.temperature,
                )
            )
            if not result.success or not result.data:
                return None
            appraisal = AppraisalResult.model_validate(result.data)
        except (ValidationError, Exception):
            # Never let appraisal crash a tick — degrade to no change.
            return None

        # Keep only known dimensions and clamp each delta to the per-appraisal
        # bound so a single line can never swing a relationship wildly.
        clamped: dict[str, float] = {}
        for dim, delta in appraisal.deltas.items():
            if dim in DIMENSIONS:
                clamped[dim] = max(-self.max_delta, min(self.max_delta, float(delta)))
        appraisal.deltas = clamped
        if not clamped:
            return None
        return appraisal

    # -- prompt construction (NPC's own cognition only — A5) --

    def _build_prompt(
        self,
        npc_id: str,
        entity: Any | None,
        event: Any,
        known_facts: list[str] | None = None,
        memory_store: Any | None = None,
        leverage: list[str] | None = None,
    ) -> str:
        try:
            template = self.prompts.load(self.TASK_TYPE, "v1.0.0")
        except (FileNotFoundError, Exception):
            template = self._fallback_prompt()

        name = npc_id.replace("npc.", "")
        traits = list(getattr(entity, "traits", []) or []) if entity is not None else []
        faction = ""
        if entity is not None:
            faction = (getattr(entity, "attributes", {}) or {}).get("faction", "")
        persona = f"你是 {name}。"
        if traits:
            persona += "性格：" + "、".join(str(t) for t in traits) + "。"
        if faction:
            persona += f"你属于「{faction}」阵营。"

        knowledge = self._knowledge_block(known_facts)
        memories = self._memory_block(npc_id, memory_store)
        action_summary = getattr(event, "summary", "") or str(event)
        leverage_block = ("\n".join(f"- {f}" for f in leverage) if leverage
                          else "（据你所知，对方此刻并无实据撑腰——只是空口而谈。）")
        dims_list = "、".join(DIMENSIONS)

        return (
            f"{template}\n\n"
            f"## 你是谁\n{persona}\n\n"
            f"## 你所相信的（你的世界观，只有你这一方知道）\n{knowledge}\n\n"
            f"## 你记得的事\n{memories}\n\n"
            f"## 对方握有的、与你利害相关的实据/筹码\n{leverage_block}\n\n"
            f"## 刚刚发生的事（你亲眼/亲耳感知到的）\n{action_summary}\n\n"
            "## 任务\n"
            "基于你的身份、立场与你所相信的，判断这件事如何改变你对当事人的看法。判断要双向、对称：不要一"
            "遇到对方坚持、追问或施压就只会加深怀疑。\n"
            "· **一般情形**：若对方此刻坦诚、讲道理、展现善意，你的戒备(suspicion)可以**下降**、信任(trust)/"
            "敬重(respect)可以**上升**——负 suspicion / 正 trust 是正常且被鼓励的。\n"
            "· **当你正戒备对方、或正被 TA 调查/追问、而 TA 显然想从你这里得到什么**：这时**空口的软话、给台阶、"
            "保证**——一个谨慎的人会合理地打个折扣。它本身**既不该让你更信任、也不该让你更怀疑**（你的戒备"
            "大体不动；把一句示好读成'居心叵测'而加深怀疑，是多疑、不是审慎）。**唯有当对方的话有上面「实据/"
            "筹码」撑腰**时，那份让步才真有分量，应让你的戒备**松动**、甚至生出几分信任(trust)。\n"
            "只有当对方**真的**显出威胁、欺骗或敌意时才加深怀疑；纯属寒暄、与你利害无关的话几乎不改变什么。\n"
            f"维度只能用：{dims_list}。增量为 -0.3~0.3 的小数（正=增强，负=削弱）。\n"
            "## 输出\n"
            '只返回 JSON：{"belief": "<你现在对此人形成的一句看法>", '
            '"deltas": {"<维度>": <增量>}, "reason": "<简短理由>"}'
        )

    @staticmethod
    def _knowledge_block(known_facts: list[str] | None) -> str:
        if not known_facts:
            return "（没有特别的成见）"
        return "\n".join(f"- {f}" for f in known_facts)

    def _memory_block(self, npc_id: str, memory_store: Any | None) -> str:
        if memory_store is None:
            return "（没有特别的记忆）"
        try:
            memories = list(memory_store.get_all(npc_id))
        except Exception:
            return "（没有特别的记忆）"
        if not memories:
            return "（没有特别的记忆）"
        memories.sort(key=lambda m: getattr(m, "salience", 0.0), reverse=True)
        lines = [f"- {getattr(m, 'content', '')}" for m in memories[:3]]
        return "\n".join(lines)

    @staticmethod
    def _fallback_prompt() -> str:
        return (
            "你将以一个 NPC 的身份，评估某个刚刚发生的行动如何改变你对当事人的态度。"
            "只表达你自己的主观看法，不要陈述世界的客观真相。"
        )
