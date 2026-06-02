"""Schema validation tests for all core Pydantic models.

These tests ensure that valid data passes validation and invalid data
raises appropriate errors. They serve as the contract layer for the
entire system.
"""

import pytest
from verisaria.engine.schemas import (
    Action,
    ActionType,
    ArbiterOutput,
    Belief,
    CommitmentLevel,
    Conviction,
    ContentPack,
    EvidenceRef,
    Event,
    EventType,
    Memory,
    MemoryLayer,
    ParsedIntent,
    PlayerAgenda,
    SaveData,
    StateChange,
    SuggestionMode,
    WorldBookEntry,
)


# ---------------------------------------------------------------------------
# ParsedIntent
# ---------------------------------------------------------------------------

class TestParsedIntent:
    def test_minimal_valid_intent(self):
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="看看周围",
            intent_type=ActionType.LOOK,
            actor_id="player_001",
            commitment=CommitmentLevel.COMMITTED,
            confidence=0.95,
            timestamp=1,
        )
        assert intent.commitment == CommitmentLevel.COMMITTED

    def test_commitment_considering_no_action(self):
        intent = ParsedIntent(
            intent_id="intent_002",
            source="natural_language",
            raw_text="我在想...",
            intent_type=ActionType.WAIT,
            actor_id="player_001",
            commitment=CommitmentLevel.CONSIDERING,
            confidence=0.5,
            timestamp=1,
        )
        assert intent.performed_content is None

    def test_player_intent_note_isolation(self):
        intent = ParsedIntent(
            intent_id="intent_003",
            source="natural_language",
            raw_text="轻声说快走",
            intent_type=ActionType.SPEECH,
            actor_id="player_001",
            performed_content="快走",
            player_intent_note="不让卫兵听见",
            commitment=CommitmentLevel.COMMITTED,
            confidence=0.91,
            timestamp=42,
        )
        assert intent.player_intent_note == "不让卫兵听见"
        assert intent.performed_content == "快走"

    def test_confidence_out_of_range(self):
        with pytest.raises(ValueError):
            ParsedIntent(
                intent_id="intent_004",
                source="natural_language",
                raw_text="test",
                intent_type=ActionType.LOOK,
                actor_id="player_001",
                commitment=CommitmentLevel.COMMITTED,
                confidence=1.5,  # invalid
                timestamp=1,
            )


# ---------------------------------------------------------------------------
# Action
# ---------------------------------------------------------------------------

class TestAction:
    def test_speech_action_valid(self):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.SPEECH,
            params={"content": "快走"},
            tick=1,
        )
        assert action.params["content"] == "快走"

    def test_speech_action_missing_content(self):
        with pytest.raises(ValueError):
            Action(
                action_id="act_1_1",
                actor_id="player_001",
                action_type=ActionType.SPEECH,
                params={},  # missing "content"
                tick=1,
            )

    def test_movement_action_valid(self):
        action = Action(
            action_id="act_1_2",
            actor_id="player_001",
            action_type=ActionType.MOVEMENT,
            params={"to_location": "blacksmith", "to_zone": "forge_area"},
            tick=1,
        )
        assert action.params["to_location"] == "blacksmith"

    def test_combat_action_valid(self):
        action = Action(
            action_id="act_1_3",
            actor_id="player_001",
            action_type=ActionType.COMBAT,
            params={"verb": "attack", "target": "npc.guard_b"},
            tick=1,
        )
        assert action.action_type == ActionType.COMBAT


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------

class TestEvent:
    def test_valid_neutral_event(self):
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            target_id="npc.ele",
            tick=1,
            location_id="town_square",
            summary="player_001 对 npc.ele 低声说话",
            canonical_facts={"content": "快走", "volume": "low"},
        )
        assert event.summary == "player_001 对 npc.ele 低声说话"

    def test_event_with_subjective_motive_rejected(self):
        with pytest.raises(ValueError):
            Event(
                event_id="evt_1_2",
                event_type=EventType.SOCIAL,
                actor_id="player_001",
                tick=1,
                location_id="town_square",
                summary="player_001 因为嫉妒而攻击 npc.guard_b",  # contains "因为"
            )

    def test_event_immutability_simulated(self):
        """Events are conceptually immutable; Pydantic models enforce this
        by convention (no setters called in production code)."""
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.MOVEMENT,
            actor_id="player_001",
            tick=1,
            location_id="town_square",
            summary="player_001 走向铁匠铺",
        )
        # Pydantic v2 models are frozen by default if configured,
        # but we leave them mutable for test convenience.
        # Production code must never modify an Event after creation.
        assert event.tick == 1


# ---------------------------------------------------------------------------
# Memory & Belief
# ---------------------------------------------------------------------------

class TestMemory:
    def test_working_memory_valid(self):
        mem = Memory(
            memory_id="mem_001",
            owner_id="npc.guard_b",
            source_observation_id="obs_001",
            tick=42,
            content="看见玩家对同伴低声说了什么",
            salience=0.3,
            decay_rate=0.05,
            layer=MemoryLayer.WORKING,
            topic_tags=["player_behavior", "suspicious_action"],
        )
        assert mem.layer == MemoryLayer.WORKING

    def test_memory_layer_transition(self):
        mem = Memory(
            memory_id="mem_050",
            owner_id="npc.guard_b",
            tick=150,
            content="玩家多次在夜间独自前往教堂方向",
            salience=0.6,
            decay_rate=0.02,
            layer=MemoryLayer.SHORT_TERM,
            topic_tags=["player_behavior", "night_activity", "church"],
            compression_of=["mem_012", "mem_023", "mem_034"],
        )
        assert mem.compression_of is not None


class TestBelief:
    def test_belief_with_conviction(self):
        belief = Belief(
            belief_id="bel_001",
            owner_id="npc.guard_b",
            claim="player_001 可能在计划什么",
            confidence=0.35,
            conviction=Conviction.LOW,
            source_evidence=["mem_001"],
            would_revise_if=["player_001 explains openly"],
            formed_at_tick=42,
            last_updated_tick=42,
        )
        assert belief.conviction == Conviction.LOW

    def test_dogmatic_belief_resistant(self):
        belief = Belief(
            belief_id="bel_002",
            owner_id="npc.church_elder",
            claim="恶魔必须被驱逐",
            confidence=0.95,
            conviction=Conviction.DOGMATIC,
            source_evidence=["mem_childhood_001", "mem_sermon_003"],
            formed_at_tick=10,
            last_updated_tick=100,
        )
        assert belief.conviction == Conviction.DOGMATIC


# ---------------------------------------------------------------------------
# Player Agenda
# ---------------------------------------------------------------------------

class TestPlayerAgenda:
    def test_agenda_with_drives(self):
        agenda = PlayerAgenda(
            player_id="player_001",
            tick=42,
            current_drives=[
                {
                    "id": "drive_survive",
                    "label": "先解决温饱",
                    "strength": 0.8,
                    "source": "player_declared",
                    "declared_at_tick": 1,
                }
            ],
            private_intent=["查清教会是否在撒谎"],
            suggestion_mode=SuggestionMode.SUBTLE,
        )
        assert agenda.suggestion_mode == SuggestionMode.SUBTLE
        assert len(agenda.current_drives) == 1


# ---------------------------------------------------------------------------
# Content Pack
# ---------------------------------------------------------------------------

class TestContentPack:
    def test_frontier_town_pack(self):
        pack = ContentPack(
            content_pack_id="frontier_town",
            world_premise={"era": "medieval_fantasy", "tone": "gritty", "central_tension": "post_war_tensions"},
            starting_location="town_square",
            world_book=[
                WorldBookEntry(
                    entry_id="demon_existence",
                    layer="canonical_fact",
                    content="恶魔族群确实存在，拥有独立的语言、文明和社会结构。",
                    access=WorldBookEntry.model_fields["access"].default,
                    confidence_policy="fact",
                )
            ],
        )
        assert pack.schema_version == "2.0"
        assert len(pack.world_book) == 1


# ---------------------------------------------------------------------------
# Arbiter Output
# ---------------------------------------------------------------------------

class TestArbiterOutput:
    def test_valid_arbiter_output(self):
        output = ArbiterOutput(
            arbiter_id="arb_001",
            source_action_id="act_001",
            outcome="partial_success",
            reason="卫兵紧张且贪财，但周围有人",
            evidence_refs=[
                EvidenceRef(path="npc.guard_b.traits.anxious", value=True, source="trait"),
                EvidenceRef(path="npc.guard_b.attributes.greed", value=0.45, source="attribute"),
            ],
            state_changes_proposed=[
                StateChange(field="npc.guard_b.alertness", delta=0.2, reason="被搭话引起警觉")
            ],
            confidence=0.78,
            narration_hint="卫兵警惕地看了你一眼",
        )
        assert output.outcome == "partial_success"

    def test_evidence_ref_invalid_path(self):
        with pytest.raises(ValueError):
            EvidenceRef(path="invalid-field-name", value=0.5, source="trait")

    def test_state_change_field_format(self):
        with pytest.raises(ValueError):
            StateChange(
                field="invalid-field-name",  # contains hyphen
                delta=0.2,
                reason="test",
            )

    def test_redirect_requires_action_type(self):
        with pytest.raises(ValueError):
            ArbiterOutput(
                arbiter_id="arb_002",
                source_action_id="act_002",
                outcome="redirect",
                reason="建议改用说服",
                confidence=0.9,
            )

    def test_redirect_with_action_type_valid(self):
        output = ArbiterOutput(
            arbiter_id="arb_003",
            source_action_id="act_003",
            outcome="redirect",
            reason="直接攻击会失败，建议先恐吓",
            redirect_to_action_type=ActionType.SOCIAL,
            confidence=0.85,
        )
        assert output.redirect_to_action_type == ActionType.SOCIAL


# ---------------------------------------------------------------------------
# Save / Load
# ---------------------------------------------------------------------------

class TestSaveData:
    def test_save_data_structure(self):
        save = SaveData(
            save_id="save_001",
            save_type="manual",
            created_at="2026-05-26T18:35:06+08:00",
            tick=42,
            rng_state="seed:42,consumed:0",
            llm_fixture_version="frontier_town_v1.0.0",
            content_pack_id="frontier_town",
            content_pack_version="1.0.0",
        )
        assert save.tick == 42
        assert save.rng_state == "seed:42,consumed:0"
