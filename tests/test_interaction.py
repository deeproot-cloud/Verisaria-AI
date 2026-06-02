"""Tests for Action Composer and Interaction Service."""

import pytest

from verisaria.engine.interaction import ActionComposer, InteractionResult, InteractionService
from verisaria.engine.schemas import ActionType, CommitmentLevel, ParsedIntent


@pytest.fixture
def composer() -> ActionComposer:
    return ActionComposer()


class TestActionComposer:
    def test_committed_speech(self, composer: ActionComposer):
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="快离开这儿",
            intent_type=ActionType.SPEECH,
            actor_id="player_001",
            target_id="npc.ele",
            content="快离开这儿",
            modifiers={"volume": "low", "emotion": "nervous"},
            commitment=CommitmentLevel.COMMITTED,
            confidence=0.95,
            performed_content="快离开这儿",
            timestamp=1,
        )
        action = composer.compose(intent, tick=1, seq=1)
        assert action is not None
        assert action.action_type == ActionType.SPEECH
        assert action.params["content"] == "快离开这儿"
        assert action.params["volume"] == "low"
        assert action.params["emotion"] == "nervous"
        assert action.target_id == "npc.ele"
        assert action.action_id == "act_1_1"

    def test_committed_movement(self, composer: ActionComposer):
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="去市场角落",
            intent_type=ActionType.MOVEMENT,
            actor_id="player_001",
            modifiers={"to_location": "town_square", "to_zone": "market_corner"},
            commitment=CommitmentLevel.COMMITTED,
            confidence=0.95,
            timestamp=1,
        )
        action = composer.compose(intent, tick=2, seq=3)
        assert action is not None
        assert action.action_type == ActionType.MOVEMENT
        assert action.params["to_location"] == "town_square"
        assert action.params["to_zone"] == "market_corner"
        assert action.action_id == "act_2_3"

    def test_movement_toward_entity_resolves_location(self, composer: ActionComposer):
        from verisaria.engine.world import EntityState, WorldState, LocationState
        world = WorldState()
        world.entities["npc.guard_b"] = EntityState(
            entity_id="npc.guard_b", entity_type="npc", location_id="tavern"
        )
        world.locations["tavern"] = LocationState(location_id="tavern")
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="悄悄靠近guard b",
            intent_type=ActionType.MOVEMENT,
            actor_id="player_001",
            target_id="npc.guard_b",
            commitment=CommitmentLevel.COMMITTED,
            confidence=0.9,
            timestamp=1,
        )
        action = composer.compose(intent, tick=1, seq=1, world=world)
        assert action is not None
        assert action.action_type == ActionType.MOVEMENT
        assert action.params["to_location"] == "tavern"

    def test_look_intent_maps_to_physical(self, composer: ActionComposer):
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="看看周围",
            intent_type=ActionType.LOOK,
            actor_id="player_001",
            commitment=CommitmentLevel.COMMITTED,
            confidence=0.98,
            timestamp=1,
        )
        action = composer.compose(intent, tick=1, seq=1)
        assert action is not None
        assert action.action_type == ActionType.PHYSICAL
        assert action.params["verb"] == "look"

    def test_considering_returns_none(self, composer: ActionComposer):
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="我在想...",
            intent_type=ActionType.WAIT,
            actor_id="player_001",
            commitment=CommitmentLevel.CONSIDERING,
            confidence=0.5,
            timestamp=1,
        )
        action = composer.compose(intent, tick=1, seq=1)
        assert action is None

    def test_attempting_composes_action(self, composer: ActionComposer):
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="我试着偷短剑",
            intent_type=ActionType.PHYSICAL,
            actor_id="player_001",
            commitment=CommitmentLevel.ATTEMPTING,
            confidence=0.65,
            performed_content="我试着偷短剑",
            timestamp=1,
        )
        action = composer.compose(intent, tick=1, seq=1)
        assert action is not None
        assert action.action_type == ActionType.PHYSICAL
        assert action.params["verb"] == "steal"

    def test_quick_command_examine(self, composer: ActionComposer):
        intent = ParsedIntent(
            intent_id="intent_001",
            source="quick_command",
            raw_text="/examine sword",
            intent_type=ActionType.LOOK,
            actor_id="player_001",
            target_ref="sword",
            commitment=CommitmentLevel.COMMITTED,
            confidence=0.99,
            performed_content="检视 sword",
            timestamp=1,
        )
        action = composer.compose(intent, tick=1, seq=1)
        assert action is not None
        assert action.action_type == ActionType.PHYSICAL
        assert action.params["verb"] == "look"

    def test_infer_verb_from_content(self, composer: ActionComposer):
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="爬墙",
            intent_type=ActionType.PHYSICAL,
            actor_id="player_001",
            commitment=CommitmentLevel.COMMITTED,
            confidence=0.8,
            timestamp=1,
        )
        action = composer.compose(intent, tick=1, seq=1)
        assert action is not None
        assert action.params["verb"] == "climb"

    def test_social_intent(self, composer: ActionComposer):
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="说服守卫放我走",
            intent_type=ActionType.SOCIAL,
            actor_id="player_001",
            target_id="npc.guard_b",
            commitment=CommitmentLevel.COMMITTED,
            confidence=0.85,
            timestamp=1,
        )
        action = composer.compose(intent, tick=1, seq=1)
        assert action is not None
        assert action.action_type == ActionType.SOCIAL
        assert action.params["verb"] == "persuade"
        assert action.params["target"] == "npc.guard_b"

    def test_greeting_infers_greet_verb(self, composer: ActionComposer):
        # A greeting must infer a non-contest verb so it skips the arbiter. (P0.4)
        for text in ("和ele打个招呼", "向守卫问好", "跟她寒暄几句"):
            intent = ParsedIntent(
                intent_id="intent_001",
                source="natural_language",
                raw_text=text,
                intent_type=ActionType.SOCIAL,
                actor_id="player_001",
                target_id="npc.ele",
                commitment=CommitmentLevel.COMMITTED,
                confidence=0.8,
                timestamp=1,
            )
            action = composer.compose(intent, tick=1, seq=1)
            assert action is not None
            assert action.params["verb"] == "greet", f"{text!r} → {action.params['verb']}"

    def test_combat_intent(self, composer: ActionComposer):
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="攻击守卫",
            intent_type=ActionType.COMBAT,
            actor_id="player_001",
            target_id="npc.guard_b",
            commitment=CommitmentLevel.COMMITTED,
            confidence=0.9,
            timestamp=1,
        )
        action = composer.compose(intent, tick=1, seq=1)
        assert action is not None
        assert action.action_type == ActionType.COMBAT
        assert action.params["verb"] == "attack"
        assert action.params["target"] == "npc.guard_b"


class TestInteractionService:
    def test_process_committed_intent(self):
        service = InteractionService()
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="看看周围",
            intent_type=ActionType.LOOK,
            actor_id="player_001",
            commitment=CommitmentLevel.COMMITTED,
            confidence=0.98,
            timestamp=1,
        )
        result = service.process_intent(intent, tick=1)
        assert result.action is not None
        assert not result.preview_only
        assert not result.clarification_needed

    def test_process_considering_returns_no_action(self):
        service = InteractionService()
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="我在想...",
            intent_type=ActionType.WAIT,
            actor_id="player_001",
            commitment=CommitmentLevel.CONSIDERING,
            confidence=0.5,
            timestamp=1,
        )
        result = service.process_intent(intent, tick=1)
        assert result.action is None
        assert not result.preview_only

    def test_process_preparing_returns_preview(self):
        service = InteractionService()
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="我准备偷偷溜过去",
            intent_type=ActionType.PHYSICAL,
            actor_id="player_001",
            modifiers={"to_location": "blacksmith", "to_zone": "storage", "stealth": True},
            commitment=CommitmentLevel.PREPARING,
            confidence=0.75,
            timestamp=1,
        )
        result = service.process_intent(intent, tick=1)
        assert result.action is not None
        assert result.preview_only
        assert result.action.action_type == ActionType.PHYSICAL
