"""System Hint / Suggestion mode: rule-driven contextual hints for the player.

P1-6 minimal version: no LLM, purely rule-based.
The system observes world state and proposes actions without mutating state (A2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from verisaria.engine.schemas import SuggestionMode
from verisaria.engine.world import WorldState


# ---------------------------------------------------------------------------
# Hint Context
# ---------------------------------------------------------------------------

@dataclass
class HintContext:
    """Read-only snapshot of relevant world state for hint generation."""

    player_id: str
    location_id: str
    zone_id: str | None
    nearby_entities: list[str]  # other entities in same location
    connected_locations: list[str]
    in_combat: bool = False
    active_conversation: Any | None = None
    confirmed_drives: list[dict[str, Any]] = field(default_factory=list)
    recent_events: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Hint System
# ---------------------------------------------------------------------------

class HintSystem:
    """Generate contextual player hints based on world state.

    Modes:
        NONE    – no hints generated.
        SUBTLE  – only high-priority hints (combat, conversation, agenda).
        NORMAL  – subtle + exploration and event-driven hints.
        GUIDED  – normal + exhaustive list of available actions.
    """

    def __init__(self, suggestion_mode: SuggestionMode = SuggestionMode.SUBTLE) -> None:
        self.suggestion_mode = suggestion_mode

    # -- Public API --

    def generate_hint(self, context: HintContext) -> str | None:
        """Return a hint string or None if mode is NONE or nothing to suggest."""
        if self.suggestion_mode == SuggestionMode.NONE:
            return None

        hints: list[str] = []

        # Priority 1: combat
        if context.in_combat:
            hints.append(self._combat_hint(context))

        # Priority 2: conversation
        if context.active_conversation is not None:
            hints.append(self._conversation_hint(context))

        # Priority 3: agenda / drives
        agenda = self._agenda_hint(context)
        if agenda:
            hints.append(agenda)

        # Priority 4: exploration (normal+)
        if self.suggestion_mode in (SuggestionMode.NORMAL, SuggestionMode.GUIDED):
            explore = self._exploration_hint(context)
            if explore:
                hints.append(explore)

            event = self._event_hint(context)
            if event:
                hints.append(event)

        # Priority 5: guided extras
        if self.suggestion_mode == SuggestionMode.GUIDED:
            guided = self._guided_hint(context)
            if guided:
                hints.append(guided)

        if not hints:
            return None

        header = "【系统建议】"
        return header + "\n" + "\n".join(f"  • {h}" for h in hints)

    def set_mode(self, mode: SuggestionMode) -> None:
        self.suggestion_mode = mode

    # -- Hint Builders --

    def _combat_hint(self, ctx: HintContext) -> str:
        """Combat-state hint."""
        return "你正处于战斗中。可用指令：攻击、防御、闪避、逃跑 (/flee)。"

    def _conversation_hint(self, ctx: HintContext) -> str:
        """Conversation-state hint."""
        session = ctx.active_conversation
        parts = [p for p in session.participants if p != ctx.player_id]
        partner = parts[0] if parts else "对方"
        turns = getattr(session, "turn_count", 0)

        if turns == 0:
            return f"你正在与 {partner} 交谈。试着说些什么，或输入 /endtalk 结束对话。"
        return f"你与 {partner} 的对话已进行 {turns} 轮。可继续交谈或 /endtalk 结束。"

    def _agenda_hint(self, ctx: HintContext) -> str | None:
        """Agenda-driven hint."""
        if not ctx.confirmed_drives:
            return None

        # Pick the highest-strength drive
        drives = sorted(
            ctx.confirmed_drives,
            key=lambda d: d.get("strength", 0.5),
            reverse=True,
        )
        top = drives[0]
        label = top.get("label", "某个目标")

        if self.suggestion_mode == SuggestionMode.SUBTLE:
            return f"你似乎对「{label}」很在意……"
        return f"当前主要驱动：{label}。考虑下一步行动如何推进此目标。"

    def _exploration_hint(self, ctx: HintContext) -> str | None:
        """Location and entity exploration hint."""
        hints: list[str] = []

        if ctx.nearby_entities:
            names = ", ".join(ctx.nearby_entities[:3])
            hints.append(f"附近有你认识的：{names}。")

        if ctx.connected_locations:
            locs = ", ".join(ctx.connected_locations[:3])
            hints.append(f"可前往：{locs}。")

        if not hints:
            return None
        return "探索提示：" + " ".join(hints)

    def _event_hint(self, ctx: HintContext) -> str | None:
        """Recent event-driven hint."""
        if not ctx.recent_events:
            return None

        latest = ctx.recent_events[-1]
        event_type = getattr(latest, "event_type", None)
        summary = getattr(latest, "summary", "")

        if event_type is None:
            return None

        et = event_type.value if hasattr(event_type, "value") else str(event_type)

        if et == "combat":
            return "注意：最近发生了战斗。检查你的状态 (/status)。"
        if et == "movement":
            return "注意：有实体刚刚移动。"
        if et == "speech":
            return f"你听到有人说话：「{summary[:30]}……」"
        return None

    def _guided_hint(self, ctx: HintContext) -> str | None:
        """Extra detail for GUIDED mode."""
        options: list[str] = []

        options.append("环顾四周 (look)")
        if ctx.nearby_entities:
            options.append("与附近的人交谈 (talk)")
        if ctx.connected_locations:
            options.append("移动到新的地点 (go)")
        if not ctx.in_combat and ctx.nearby_entities:
            options.append("发起战斗 (attack)")
        options.append("等待 (wait)")

        return "所有可用行动类型：" + "、".join(options) + "。"
