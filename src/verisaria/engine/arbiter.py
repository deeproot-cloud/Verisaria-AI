"""LLM Arbiter: soft arbitration for actions that need subjective interpretation.

Responsibilities:
- Build arbitration context from world state
- Call LLM for narrative裁决
- Validate output against ArbiterOutput schema
- Pass through State Validator
- Fallback to Rules Engine default on failure
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from verisaria.engine.llm import LLMCallRequest, LLMOrchestrator
from verisaria.engine.schemas import Action, ArbiterOutput
from verisaria.engine.validator import StateValidator, ValidatedOutcome
from verisaria.engine.world import WorldCore
from verisaria.engine.world_book_filter import WorldBookFilter


# ---------------------------------------------------------------------------
# Arbiter Context
# ---------------------------------------------------------------------------

@dataclass
class ArbiterContext:
    action: Action
    actor_attributes: dict[str, Any]
    target_attributes: dict[str, Any] | None
    location_id: str
    zone_id: str | None
    recent_events: list[dict[str, Any]]
    world_book_entries: list[str]  # filtered by actor scope
    # Pack-declared mutable world facts the arbiter may propose changing
    # (PLAY-3 Channel C, slice 1b), e.g. {"var_id", "label", "current", "set_by"}.
    mutable_world_vars: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# LLM Arbiter
# ---------------------------------------------------------------------------

class LLMArbiter:
    """Arbitrate actions requiring subjective interpretation."""

    def __init__(
        self,
        llm_orchestrator: LLMOrchestrator,
    ) -> None:
        self.llm = llm_orchestrator
        self._seq = 0

    def arbitrate(
        self,
        action: Action,
        world: WorldCore,
    ) -> ValidatedOutcome:
        """Arbitrate an action and return a validated outcome.

        On LLM failure, returns a fallback outcome with default rules.
        """
        self._seq += 1
        arbiter_id = f"arb_{action.tick}_{self._seq}"

        # Build context
        context = self._build_context(action, world)

        # Build prompt
        prompt = self._build_prompt(context)

        # Call LLM
        result = self.llm.call(
            LLMCallRequest(
                task_type="arbiter_decide",
                prompt=prompt,
                schema_model=ArbiterOutput,
                model_preference="gpt",  # Arbiter requires high stability
            )
        )

        if not result.success:
            # Fallback: deterministic outcome based on action type
            return self._fallback_outcome(arbiter_id, action)

        arbiter_output = ArbiterOutput.model_validate(result.data)

        # Ensure arbiter_id matches
        # (LLM might not generate the correct ID, so we override)
        # Note: Pydantic v2 models are frozen by default if configured,
        # but we leave them mutable for this override.
        arbiter_output.arbiter_id = arbiter_id
        arbiter_output.source_action_id = action.action_id

        # Build context for State Validator
        validator_context = self._build_validator_context(context, world)
        validator = StateValidator(context=validator_context)

        return validator.validate(arbiter_output)

    def _build_context(self, action: Action, world: WorldCore) -> ArbiterContext:
        """Build arbitration context from world state."""
        actor = world.state.get_entity(action.actor_id)
        target = world.state.get_entity(action.target_id) if action.target_id else None

        # Recent events (last 5 from same location)
        recent = [
            {
                "event_id": e.event_id,
                "event_type": e.event_type.value,
                "summary": e.summary,
            }
            for e in world.event_log.get_events(since_tick=max(0, world.state.tick - 5))
            if e.location_id == (actor.location_id if actor else "")
        ][-5:]

        # Filter world book by actor scope
        world_book = getattr(world, 'world_book', None)
        if world_book is None and hasattr(world, 'state') and hasattr(world.state, 'locations'):
            # Try to get world_book from content pack via state
            pass
        # For now, world_book is not stored on WorldCore/WorldState.
        # In practice it comes from the content pack. We accept it as an
        # optional injected attribute for testing.
        raw_entries = getattr(world, 'world_book_entries', [])
        filtered = WorldBookFilter.filter_for_entity(raw_entries, actor)

        return ArbiterContext(
            action=action,
            actor_attributes=actor.attributes if actor else {},
            target_attributes=target.attributes if target else None,
            location_id=actor.location_id if actor else "unknown",
            zone_id=actor.zone_id if actor else None,
            recent_events=recent,
            world_book_entries=[e.content for e in filtered],
            mutable_world_vars=list(getattr(world, "mutable_world_vars", []) or []),
        )

    def _build_prompt(self, context: ArbiterContext) -> str:
        """Build the arbitration prompt."""
        action = context.action
        actor_attrs = context.actor_attributes
        target_attrs = context.target_attributes

        prompt = f"""你是一名公正的仲裁者，需要判定一个角色行动的结果。

## 行动

- 行动者: {action.actor_id}
- 行动类型: {action.action_type.value}
- 参数: {action.params}
- 目标: {action.target_id or "无"}

## 行动者属性

{actor_attrs}

"""
        if target_attrs:
            prompt += f"""## 目标属性

{target_attrs}

"""

        if context.recent_events:
            prompt += "## 最近事件\n\n"
            for evt in context.recent_events:
                prompt += f"- {evt['summary']}\n"
            prompt += "\n"

        if context.mutable_world_vars:
            prompt += (
                "## 可改变的世界状态\n\n"
                "若此行动正当地改变了下列世界事实，可在 state_changes_proposed "
                "中提议（field 写成 `world.<变量名>`）。**只有当持此权限的 NPC，基于其"
                "对当事人的态度与自身职责，会同意时**才提议变更；否则维持现状（并让该 "
                "NPC 给出符合身份的回应理由）。\n"
                "某些变量下会列出【先前已确立】的中间事实——早先交涉里这位 NPC 松过的口或"
                "提过的条件。若当前请求显示这些条件**现在已被满足**，可据此判 success；但"
                "中间事实本身不自动构成成功，且当事人若已背弃信任，你也可推翻先前的让步。\n"
                "判断条件是否满足时，要把【其它世界变量的当前值】和【其它变量下已确立的事实】"
                "也纳入考量：若某项前置已由别处交涉达成（例如所需的世界事实当前已为真，或另一"
                "变量下已记录了对应的让步），即可视为该条件满足——不要因为'只是口头声称'就忽略"
                "这些已被结构化记录的既成事实。\n"
            )
            for v in context.mutable_world_vars:
                var_id = v.get("var_id", "")
                label = v.get("label", var_id)
                current = v.get("current")
                set_by = v.get("set_by")
                auth = v.get("authority_npc")
                rel = v.get("authority_relationship")
                line = f"- `world.{var_id}`（{label}）：当前 = {current}"
                if set_by:
                    line += f"；需 {set_by} 批准"
                if auth:
                    line += f"；持此权限者：{auth}"
                    if rel:
                        line += f"；{auth}对当事人的态度：{rel}"
                prompt += line + "\n"
                for fact in (v.get("established_facts") or []):
                    prompt += f"    · （先前已确立）{fact}\n"
            prompt += "\n"

        prompt += """## 输出要求

返回 JSON，格式如下：
{
  "outcome": "success" | "partial_success" | "failure",
  "reason": "裁决理由（100字以内）",
  "evidence_refs": [
    {"path": "字段路径", "value": "值", "source": "trait|attribute|world_state|relationship"}
  ],
  "state_changes_proposed": [
    {"field": "字段路径", "delta": 数值, "reason": "变更原因"}
  ],
  "confidence": 0.0-1.0,
  "narration_hint": "给叙事者的提示（50字以内）",
  "established_fact": "仅当 outcome 为 partial_success：用一句客观陈述写下此刻已确立的中间事实或条件（供日后裁定复用），如「他愿意交出报告，前提是匿名」。务必写成【可满足、可闭环】的条件，写清楚对方还具体需要什么，避免「稍后审议」「改天再说」这类无法被后续满足的表述；其它情况留空字符串"
}
"""
        return prompt

    def _build_validator_context(self, context: ArbiterContext, world: WorldCore) -> dict[str, Any]:
        """Build context dict for State Validator from real world state."""
        # Build locations dict from world state
        locations_ctx: dict[str, Any] = {}
        for loc_id, loc in world.state.locations.items():
            locations_ctx[loc_id] = {"zones": {}}
            for zone_id, zone in loc.zones.items():
                locations_ctx[loc_id]["zones"][zone_id] = {
                    "visibility": zone.visibility,
                    "exposure": zone.exposure,
                    "noise_level": zone.noise_level,
                }

        # Build npc dict from world state
        npc_ctx: dict[str, Any] = {}
        # Flat entity dict carries hp/max_hp for the validator's consistency
        # checks (e.g. rejecting hp set above max_hp).
        entities_ctx: dict[str, Any] = {}
        for entity_id, entity in world.state.entities.items():
            entities_ctx[entity_id] = {
                "hp": entity.hp,
                "max_hp": entity.max_hp,
                "stamina": entity.stamina,
            }
            if entity_id.startswith("npc."):
                npc_id = entity_id.replace("npc.", "")
                npc_ctx[npc_id] = {
                    "attributes": entity.attributes,
                    "traits": {t: True for t in entity.traits},
                }

        return {
            "entities": entities_ctx,
            "actor": {
                "id": context.action.actor_id,
                "attributes": context.actor_attributes,
            },
            "target": {
                "id": context.action.target_id,
                "attributes": context.target_attributes or {},
            },
            "location": {
                "id": context.location_id,
                "zone": context.zone_id,
            },
            "world_state": {
                "locations": locations_ctx,
            },
            "npc": npc_ctx,
        }

    def _fallback_outcome(self, arbiter_id: str, action: Action) -> ValidatedOutcome:
        """Deterministic fallback when LLM fails."""
        from verisaria.engine.schemas import StateChange

        # Default outcomes by action type
        if action.action_type.value == "social":
            outcome = "partial_success"
            reason = "LLM 不可用，按默认规则：社交行动结果不确定。"
        elif action.action_type.value == "physical":
            outcome = "failure"
            reason = "LLM 不可用，按默认规则：需要技巧的行动失败。"
        elif action.action_type.value == "combat":
            outcome = "failure"
            reason = "LLM 不可用，按默认规则：战斗未命中。"
        else:
            outcome = "failure"
            reason = "LLM 不可用，按默认规则处理。"

        arbiter_output = ArbiterOutput(
            arbiter_id=arbiter_id,
            source_action_id=action.action_id,
            outcome=outcome,  # type: ignore[arg-type]
            reason=reason,
            evidence_refs=[],
            state_changes_proposed=[],
            confidence=0.5,
            narration_hint="系统默认裁决。",
            is_fallback=True,  # not a real verdict — LLM was unavailable
        )

        return ValidatedOutcome(
            accepted=True,
            arbiter_output=arbiter_output,
            accepted_state_changes=[],
            rejected_state_changes=[],
            issues=[],
        )
