"""Tests for Subjectivity Service: Interpretation, Memory conversion, pipeline."""

from __future__ import annotations

import pytest

from verisaria.engine.schemas import (
    Interpretation,
    MemoryLayer,
    Observation,
    Perception,
)
from verisaria.engine.memory import (
    BeliefEngine,
    MemoryStore,
)
from verisaria.engine.subjectivity import (
    InterpretationGenerator,
    MemoryConverter,
    SubjectivityService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def subjectivity_service() -> SubjectivityService:
    store = MemoryStore()
    store.clear()
    engine = BeliefEngine()
    engine.clear()
    return SubjectivityService(store, engine)


def make_observation(
    observer_id: str = "npc.guard",
    tick: int = 1,
    detail_level: str = "full",
    perception_kwargs: dict | None = None,
) -> Observation:
    kwargs = perception_kwargs or {}
    return Observation(
        observation_id="obs_001",
        observer_id=observer_id,
        source_event_id="evt_001",
        tick=tick,
        perception=Perception(**kwargs),
        interpretation=None,
        detail_level=detail_level,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Interpretation Generator
# ---------------------------------------------------------------------------

class TestInterpretationGenerator:
    def test_full_perception_high_confidence(self) -> None:
        gen = InterpretationGenerator()
        perception = Perception(
            channels=["sight", "hearing"],
            saw="玩家走向铁匠铺",
            heard_full_content=True,
            distance="near",
            attention_level="focused",
        )
        interp = gen.generate(perception, event_summary="玩家走向铁匠铺并交谈")
        assert interp.confidence >= 0.8
        assert "看见" in interp.claim
        assert interp.emotional_tone is None

    def test_claim_dedupes_same_sight_and_hearing(self) -> None:
        # When sight and hearing carry the same summary, the claim states it
        # once ("看见并听到：X") instead of "看见：X；听到：X". (P1.3)
        gen = InterpretationGenerator()
        perception = Perception(
            channels=["sight", "hearing"],
            saw="player_001 对 npc.guard_b 说话",
            heard_full_content=True,
            distance="near",
            attention_level="focused",
        )
        interp = gen.generate(
            perception, event_summary="player_001 对 npc.guard_b 说话"
        )
        assert "看见并听到：" in interp.claim
        assert interp.claim.count("player_001 对 npc.guard_b 说话") == 1

    def test_partial_perception_low_confidence(self) -> None:
        gen = InterpretationGenerator()
        perception = Perception(
            channels=["hearing"],
            heard_keywords=["钱", "离开"],
            heard_full_content=False,
            distance="far",
            attention_level="distracted",
        )
        interp = gen.generate(perception)
        assert interp.confidence < 0.5
        assert len(interp.alternatives) > 0
        assert "关键词" in interp.claim

    def test_paranoia_trait_reduces_confidence(self) -> None:
        gen = InterpretationGenerator()
        perception = Perception(
            channels=["sight"],
            saw="玩家走向铁匠铺",
            distance="near",
            attention_level="focused",
        )
        normal = gen.generate(perception, observer_traits=["calm"])
        paranoid = gen.generate(perception, observer_traits=["paranoid"])
        assert paranoid.confidence < normal.confidence

    def test_perceptive_trait_increases_confidence(self) -> None:
        gen = InterpretationGenerator()
        perception = Perception(
            channels=["sight"],
            saw="玩家走向铁匠铺",
            distance="near",
            attention_level="focused",
        )
        normal = gen.generate(perception)
        perceptive = gen.generate(perception, observer_traits=["perceptive"])
        assert perceptive.confidence > normal.confidence

    def test_emotional_tone_from_traits(self) -> None:
        gen = InterpretationGenerator()
        perception = Perception(
            channels=["sight"],
            saw="玩家走向铁匠铺",
            distance="near",
            attention_level="focused",
        )
        interp = gen.generate(perception, observer_traits=["anxious"])
        assert interp.emotional_tone == "unease"

    def test_urgency_from_keywords(self) -> None:
        gen = InterpretationGenerator()
        perception = Perception(
            channels=["hearing"],
            heard_keywords=["help", "danger"],
            heard_full_content=False,
            distance="near",
            attention_level="focused",
        )
        interp = gen.generate(perception)
        assert interp.emotional_tone == "urgency_detected"

    def test_minimal_perception_claim(self) -> None:
        gen = InterpretationGenerator()
        perception = Perception(
            channels=["hearing"],
            heard_keywords=[],
            heard_full_content=False,
            distance="far",
            attention_level="unaware",
        )
        interp = gen.generate(perception)
        assert "察觉到" in interp.claim
        assert interp.confidence < 0.5


# ---------------------------------------------------------------------------
# Memory Converter
# ---------------------------------------------------------------------------

class TestMemoryConverter:
    def test_convert_with_interpretation(self) -> None:
        conv = MemoryConverter()
        obs = make_observation(
            perception_kwargs={
                "channels": ["sight"],
                "saw": "玩家走向铁匠铺",
                "distance": "near",
                "attention_level": "focused",
            }
        )
        # Manually add interpretation
        obs = Observation(
            observation_id=obs.observation_id,
            observer_id=obs.observer_id,
            source_event_id=obs.source_event_id,
            tick=obs.tick,
            perception=obs.perception,
            interpretation=Interpretation(
                claim="玩家前往铁匠铺",
                confidence=0.8,
                alternatives=[],
                emotional_tone=None,
            ),
            detail_level=obs.detail_level,
        )
        mem = conv.convert(obs)
        assert mem.owner_id == "npc.guard"
        assert mem.layer == MemoryLayer.WORKING
        assert mem.content == "玩家前往铁匠铺"
        assert mem.salience == 0.8
        assert mem.source_observation_id == "obs_001"

    def test_convert_without_interpretation(self) -> None:
        conv = MemoryConverter()
        obs = make_observation(
            perception_kwargs={
                "channels": ["sight"],
                "saw": "玩家走向铁匠铺",
                "distance": "near",
                "attention_level": "focused",
            }
        )
        mem = conv.convert(obs)
        assert mem.layer == MemoryLayer.WORKING
        assert "看见" in mem.content
        assert mem.salience == 0.3

    def test_topic_tag_extraction(self) -> None:
        conv = MemoryConverter()
        obs = make_observation(
            perception_kwargs={
                "channels": ["sight", "hearing"],
                "saw": "玩家攻击守卫",
                "heard_keywords": ["help"],
                "distance": "near",
                "attention_level": "focused",
            }
        )
        mem = conv.convert(obs)
        assert "player" in mem.topic_tags
        assert "combat" in mem.topic_tags


# ---------------------------------------------------------------------------
# Subjectivity Service
# ---------------------------------------------------------------------------

class TestSubjectivityService:
    def test_process_observation_full_detail(self, subjectivity_service: SubjectivityService) -> None:
        obs = make_observation(
            detail_level="full",
            perception_kwargs={
                "channels": ["sight", "hearing"],
                "saw": "玩家走向铁匠铺",
                "heard_full_content": True,
                "distance": "near",
                "attention_level": "focused",
            }
        )
        result = subjectivity_service.process_observation(obs)
        assert result.interpretation is not None
        assert result.interpretation.confidence >= 0.7

    def test_process_observation_minimal_detail(self, subjectivity_service: SubjectivityService) -> None:
        obs = make_observation(
            detail_level="minimal",
            perception_kwargs={
                "channels": ["hearing"],
                "heard_keywords": [],
                "distance": "far",
                "attention_level": "unaware",
            }
        )
        result = subjectivity_service.process_observation(obs)
        assert result.interpretation is None

    def test_ingest_full_pipeline(self, subjectivity_service: SubjectivityService) -> None:
        obs = make_observation(
            detail_level="full",
            perception_kwargs={
                "channels": ["sight"],
                "saw": "玩家给了守卫一笔钱",
                "distance": "near",
                "attention_level": "focused",
            }
        )
        result = subjectivity_service.ingest(obs, observer_traits=["suspicious"])

        assert result["observation"].interpretation is not None
        assert result["memory"].owner_id == "npc.guard"
        assert result["belief_change"].change_type == "created"

        # Memory should be in store
        assert subjectivity_service.memory_store.count("npc.guard") == 1

        # Belief should be in engine
        beliefs = subjectivity_service.belief_engine.get_beliefs("npc.guard")
        assert len(beliefs) == 1

    def test_ingest_strengthens_existing_belief(self, subjectivity_service: SubjectivityService) -> None:
        # First observation
        obs1 = make_observation(
            tick=1,
            detail_level="full",
            perception_kwargs={
                "channels": ["sight"],
                "saw": "玩家帮助村民修理房屋",
                "distance": "near",
                "attention_level": "focused",
            }
        )
        subjectivity_service.ingest(obs1)

        # Second observation reinforcing the same belief
        obs2 = make_observation(
            tick=2,
            detail_level="full",
            perception_kwargs={
                "channels": ["sight"],
                "saw": "玩家帮助老人搬运货物",
                "distance": "near",
                "attention_level": "focused",
            }
        )
        result = subjectivity_service.ingest(obs2)
        assert result["belief_change"].change_type == "strengthened"

    def test_should_generate_interpretation(self, subjectivity_service: SubjectivityService) -> None:
        full = make_observation(detail_level="full", perception_kwargs={"channels": ["sight"], "saw": "x"})
        partial = make_observation(detail_level="partial", perception_kwargs={"channels": ["hearing"], "heard_keywords": ["x"]})
        minimal = make_observation(detail_level="minimal", perception_kwargs={"channels": ["hearing"]})

        assert subjectivity_service.should_generate_interpretation(full) is True
        assert subjectivity_service.should_generate_interpretation(partial) is True
        assert subjectivity_service.should_generate_interpretation(minimal) is False

    def test_update_relationship(self, subjectivity_service: SubjectivityService) -> None:
        # Create a belief about the player
        obs = make_observation(
            detail_level="full",
            perception_kwargs={
                "channels": ["sight"],
                "saw": "玩家很可靠",
                "distance": "near",
                "attention_level": "focused",
            }
        )
        subjectivity_service.ingest(obs)
        snapshot = subjectivity_service.update_relationship("npc.guard", "玩家", tick=10)
        assert snapshot is not None
        assert snapshot.dimensions["trust"] > 0

    def test_update_relationship_no_beliefs(self, subjectivity_service: SubjectivityService) -> None:
        snapshot = subjectivity_service.update_relationship("npc.guard", "玩家", tick=10)
        assert snapshot is None
