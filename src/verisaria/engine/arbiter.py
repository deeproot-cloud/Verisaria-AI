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
    # The real NPC roster {id, authority, location}, so a new_prerequisite's set_by
    # names an NPC that actually exists instead of an invented one.
    npc_roster: list[dict[str, Any]] = field(default_factory=list)
    # Set for a P2c escort request {npc_name, dest, relationship}: adjudicate plain
    # WILLINGNESS to accompany (persona + stance), NOT a world-change with
    # prerequisites — reusing the world-change framing biased escort toward refusal.
    escort: dict[str, Any] | None = None
    # The target NPC's persona traits — the author's primary "what they're like"
    # signal. Without it the escort willingness judge only saw bare attributes.
    target_traits: list[str] = field(default_factory=list)


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
            return self._fallback_outcome(arbiter_id, action, result)

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

        roster = [
            {
                "id": eid,
                "authority": (e.attributes or {}).get("authority", ""),
                "location": e.location_id,
            }
            for eid, e in world.state.entities.items()
            if e.entity_type == "npc"
        ]

        escort = getattr(world, "escort_request", None)
        return ArbiterContext(
            action=action,
            actor_attributes=actor.attributes if actor else {},
            target_attributes=target.attributes if target else None,
            target_traits=list(getattr(target, "traits", []) or []) if target else [],
            location_id=actor.location_id if actor else "unknown",
            zone_id=actor.zone_id if actor else None,
            recent_events=recent,
            world_book_entries=[e.content for e in filtered],
            # escort is a willingness judgment — no world-var/prerequisite context
            mutable_world_vars=([] if escort else list(getattr(world, "mutable_world_vars", []) or [])),
            npc_roster=roster,
            escort=escort,
        )

    def _build_prompt(self, context: ArbiterContext) -> str:
        """Build the arbitration prompt."""
        if context.escort:
            return self._build_escort_prompt(context)

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
                "你像一位尽职的 GM：**只提当事人在当前世界里够得着的条件**，不要抛出无路可走的"
                "空要求。**关键规则**：如果你之所以不批准，是因为当事人必须先达成某个【上面世界变量"
                "里没有列出的】前置——例如'先取得某编号/回执/签章''先完成某审议''先让某人到场作证或"
                "书面声明''先拿到另一机构的指令'——你【必须】在 `new_prerequisite` 里把它声明成一个"
                "新的世界变量，**不能只把它写进 reason 或 established_fact 的散文里**。\n"
                "  · established_fact 只是给你日后参考的软备忘，当事人无法据此真正完成条件；\n"
                "  · 只有声明成世界变量，当事人才有结构化路径去达成它（之后该变量翻 true 才算满足）。\n"
                "  · var_id 必须是 **ascii 蛇形命名**（如 union_pause_order_received、"
                "archive_review_completed、oro_white_bay_statement_recorded），不要用中文；\n"
                "  · set_by **只能写确实存在、当事人够得着的 NPC**（用上面列出/在场的 NPC 的真实 id，"
                "**完整形式含 `npc.` 前缀**，如 `npc.clinician_oro`；或其 authority 角色），不要凭空"
                "编造一个不存在的人，否则当事人无人可找；\n"
                "  · request_keywords 多给几个【当事人会自然说出口】的短语，便于路由。\n"
                "  · 每出现一个这样的新前置就声明一个（除非它其实等同于上面已列出的某个变量）。\n"
                "**另一种情况**：如果当事人请求的是【启动一个本身需要时间/线下完成的流程】——例如"
                "提交理事会审议、递交申请等回执、联系某机构走程序——而这个流程一旦启动就会自然走完，"
                "你不应要求当事人立刻拿出尚不存在的结果，而应判 partial_success 并在 `process_started` "
                "里把对应的动态变量标记为'已启动、N 个 tick 后自动完成'（var_id 用那个动态变量，"
                "matures_in_ticks 给一个合理的小数字）。这样流程会随时间成熟、当事人无需空等到死。\n"
                "**避免前置无限递归（很重要）**：\n"
                "  · (a) 你引入的【每一个】new_prerequisite 都必须**本身能在一两步内被满足**——找一个"
                "在场/够得着的真实 NPC、做一件当事人此刻能做的事、或走一个短流程即可达成；**绝不要**引入一个"
                "【本身又要先满足更深前置】的条件。若某件事天然要走很多道手续，就把它**整体当作一个短流程**"
                "（用 process_started 让它随时间成熟），而不是拆成一层套一层、永远到不了底的前置链。\n"
                "  · (b) **当事人已经做足铺垫时就放行**：把该变量下【已确立的中间事实】和【已为真的相关"
                "前置变量】通盘看——若它们已经覆盖了请求的**实质核心**，剩下的只是程序性手续（盖章、登记、"
                "口头确认、走个过场），就应当判 **success**，**不要**再派生新前置。一位尽职的权威在当事人"
                "人证物证齐、该跑的腿都跑齐之后会拍板办事，而不是无止境地再加一道手续。\n"
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

        if context.npc_roster:
            prompt += "## 世界中的 NPC（new_prerequisite 的 set_by 只能从这里选真实 id）\n\n"
            for n in context.npc_roster:
                role = f"（authority={n['authority']}）" if n.get("authority") else ""
                prompt += f"- {n['id']}{role} @ {n.get('location', '')}\n"
            prompt += "\n"

        prompt += """## 示例：当你因"上面没有的新前置"而不批准时（务必声明 new_prerequisite）

当事人请求暂停清洗，但你判断需先取得工会的正式停洗指令，而"工会停洗指令"不在上面的世界变量里：
{
  "outcome": "partial_success",
  "reason": "你有暂停权，但单方面暂停需有工会正式停洗指令作前置，目前没有。",
  "established_fact": "你愿暂停清洗，前提是先取得工会正式停洗指令。",
  "new_prerequisite": {"var_id": "union_pause_order_received", "label": "工会停洗指令已取得", "set_by": ["npc.union_steward"], "request_keywords": ["工会指令", "停洗指令", "工会停洗"]}
}

## 输出要求

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
  "established_fact": "仅当 outcome 为 partial_success：用一句客观陈述写下此刻已确立的中间事实或条件（供日后裁定复用），如「他愿意交出报告，前提是匿名」。务必写成【可满足、可闭环】的条件，写清楚对方还具体需要什么，避免「稍后审议」「改天再说」这类无法被后续满足的表述；其它情况留空字符串",
  "new_prerequisite": null 或 {"var_id": "snake_case_ascii_id", "label": "中文标签", "set_by": ["能满足它的NPC的id或authority角色"], "request_keywords": ["玩家可能用来达成它的说法"]}（仅当你引入了上面变量里还没有的新前置时才填，否则 null）,
  "process_started": null 或 {"var_id": "已有的某个动态变量id", "matures_in_ticks": 2}（仅当当事人启动了一个需时间/线下完成的流程时才填，否则 null）
}
"""
        return prompt

    def _build_escort_prompt(self, context: ArbiterContext) -> str:
        """A P2c escort is a plain WILLINGNESS judgment — would this NPC, given their
        persona and stance toward the player, get up and come along? — not a
        world-change requiring prerequisites. Framing it as the latter (the shared
        world-change prompt) biased escort toward 'I need X first' refusals."""
        esc = context.escort or {}
        action = context.action
        content = (action.params or {}).get("content", "") if action else ""
        rel = esc.get("relationship") or {}
        rel_str = "、".join(f"{k} {v:.2f}" for k, v in rel.items()) or "（无既有关系，初次打交道）"
        recent = "\n".join(f"- {e['summary']}" for e in context.recent_events) or "（无）"
        return f"""你是一名公正的仲裁者，判断一个 NPC 此刻是否愿意【陪同当事人前往某地】。

## 请求
- 对象 NPC：{esc.get('npc_name', '')}
- 目的地：{esc.get('dest', '')}
- 当事人原话：{content}

## 该 NPC 的人设
- 性格/特质：{("、".join(context.target_traits) if context.target_traits else "（未注明）")}
- 属性：{context.target_attributes or {}}

## 该 NPC 对当事人的态度（关系维度，0–1）
{rel_str}

## 最近发生的事
{recent}

## 判断要求
只根据这个 NPC 的【性格、对当事人的态度、当前处境与利害】，判断 TA 此刻愿不愿意起身跟着走：
- success = 愿意，当场就跟着走；
- partial_success = 没当场拒绝，但有条件或在犹豫（要个保障、要先办完手头的事…）；
- failure = 不愿意。
这是一次**社交意愿**判断，**不要**把它当成需要满足世界前置变量的请求。给真实、可信、贴合人设的
判断；当 TA 对当事人信任足够、风险不高时，就让 TA 答应同行。

返回 JSON：
{{"outcome": "success" | "partial_success" | "failure", "reason": "理由（80字内）", "confidence": 0.0-1.0, "narration_hint": "给叙事者的提示（可空）"}}
"""

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

    def _fallback_outcome(self, arbiter_id: str, action: Action,
                          result: Any = None) -> ValidatedOutcome:
        """Deterministic fallback when the LLM call fails. The ``cause`` reflects the
        REAL reason — a schema/JSON rejection of the (large) ArbiterOutput is not the
        same as the API being down, and logging it as 'LLM 不可用' was misleading."""
        from verisaria.engine.schemas import StateChange
        from verisaria.engine.llm import LLMErrorCategory

        cat = getattr(result, "error_category", None)
        if cat in (LLMErrorCategory.PARSE, LLMErrorCategory.VALIDATION):
            cause = "仲裁输出格式非法（重试后仍未通过 JSON/schema 校验）"
        elif cat == LLMErrorCategory.BUDGET:
            cause = "本 tick 仲裁预算已用尽"
        else:
            cause = "LLM 不可用"

        # Default outcomes by action type
        if action.action_type.value == "social":
            outcome = "partial_success"
            reason = f"{cause}，按默认规则：社交行动结果不确定。"
        elif action.action_type.value == "physical":
            outcome = "failure"
            reason = f"{cause}，按默认规则：需要技巧的行动失败。"
        elif action.action_type.value == "combat":
            outcome = "failure"
            reason = f"{cause}，按默认规则：战斗未命中。"
        else:
            outcome = "failure"
            reason = f"{cause}，按默认规则处理。"

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
