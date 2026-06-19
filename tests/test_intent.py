"""Tests for Intent Parser."""

import pytest

from verisaria.engine.intent import (
    ClarificationRequest,
    CoherenceChecker,
    CoherenceIssue,
    IntentParser,
    PromptLoader,
)
from verisaria.engine.llm import FakeLLMProvider, LLMCallRequest, LLMOrchestrator
from verisaria.engine.schemas import ActionType, CommitmentLevel, ParsedIntent
from verisaria.engine.world import EntityState, LocationState, WorldState, ZoneState


@pytest.fixture
def parser() -> IntentParser:
    provider = FakeLLMProvider("fixtures")
    llm = LLMOrchestrator(primary_provider=provider)
    return IntentParser(llm_orchestrator=llm)


@pytest.fixture
def world_state() -> WorldState:
    return WorldState(
        tick=1,
        entities={
            "player_001": EntityState(
                entity_id="player_001",
                entity_type="player",
                location_id="town_square",
                zone_id="center",
            ),
            "npc.ele": EntityState(
                entity_id="npc.ele",
                entity_type="npc",
                location_id="town_square",
                zone_id="center",
            ),
            "npc.guard_b": EntityState(
                entity_id="npc.guard_b",
                entity_type="npc",
                location_id="town_square",
                zone_id="center",
            ),
        },
        locations={
            "town_square": LocationState(
                location_id="town_square",
                zones={
                    "center": ZoneState(zone_id="center", location_id="town_square"),
                    "market_corner": ZoneState(
                        zone_id="market_corner", location_id="town_square"
                    ),
                },
            ),
            "blacksmith": LocationState(
                location_id="blacksmith",
                zones={
                    "forge_area": ZoneState(
                        zone_id="forge_area", location_id="blacksmith"
                    ),
                    "storage": ZoneState(
                        zone_id="storage", location_id="blacksmith"
                    ),
                },
            ),
        },
    )


class TestPromptLoader:
    def test_load_existing_prompt(self):
        loader = PromptLoader("content/prompts")
        text = loader.load("parse_player_intent", "v1.0.0")
        assert "ParsedIntent" in text
        assert "commitment" in text

    def test_load_missing_prompt_raises(self):
        loader = PromptLoader("content/prompts")
        with pytest.raises(FileNotFoundError):
            loader.load("nonexistent_task", "v999")


class TestCoherenceChecker:
    def test_valid_intent_no_issues(self, world_state: WorldState):
        checker = CoherenceChecker()
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
        issues = checker.check(intent, world_state)
        assert len(issues) == 0

    def test_target_not_found(self, world_state: WorldState):
        checker = CoherenceChecker()
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="对幽灵说话",
            intent_type=ActionType.SPEECH,
            actor_id="player_001",
            target_id="npc.ghost",
            content="你好",
            commitment=CommitmentLevel.COMMITTED,
            confidence=0.8,
            timestamp=1,
        )
        issues = checker.check(intent, world_state)
        assert len(issues) == 1
        assert issues[0].issue_type == "target_not_found"
        assert issues[0].severity == "error"

    def test_actor_not_found(self, world_state: WorldState):
        checker = CoherenceChecker()
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="看看周围",
            intent_type=ActionType.LOOK,
            actor_id="npc.unknown",
            commitment=CommitmentLevel.COMMITTED,
            confidence=0.95,
            timestamp=1,
        )
        issues = checker.check(intent, world_state)
        assert any(i.issue_type == "actor_not_found" for i in issues)

    def test_low_commitment_warning(self, world_state: WorldState):
        checker = CoherenceChecker()
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="我在想...",
            intent_type=ActionType.SPEECH,
            actor_id="player_001",
            content="也许该离开",
            commitment=CommitmentLevel.CONSIDERING,
            confidence=0.5,
            timestamp=1,
        )
        issues = checker.check(intent, world_state)
        assert any(i.issue_type == "low_commitment_with_action" for i in issues)
        assert all(i.severity == "warning" for i in issues if i.issue_type == "low_commitment_with_action")


class TestIntentParser:
    def test_parse_look_around(self, parser: IntentParser, world_state: WorldState):
        result = parser.parse(
            raw_text="看看周围",
            actor_id="player_001",
            tick=1,
            world=world_state,
        )
        assert isinstance(result, ParsedIntent)
        assert result.intent_type == ActionType.LOOK
        assert result.actor_id == "player_001"
        assert result.commitment == CommitmentLevel.COMMITTED

    def test_parse_whisper_with_intent_note(self, parser: IntentParser, world_state: WorldState):
        result = parser.parse(
            raw_text="我低声对艾蕾说，我们先快离开这儿",
            actor_id="player_001",
            tick=1,
            world=world_state,
        )
        assert isinstance(result, ParsedIntent)
        assert result.intent_type == ActionType.SPEECH
        assert result.target_id == "npc.ele"
        assert result.performed_content == "我们先快离开这儿"
        assert result.player_intent_note == "不让卫兵听见"
        assert result.modifiers.get("volume") == "low"

    def test_parse_movement(self, parser: IntentParser, world_state: WorldState):
        result = parser.parse(
            raw_text="去市场角落",
            actor_id="player_001",
            tick=1,
            world=world_state,
        )
        assert isinstance(result, ParsedIntent)
        assert result.intent_type == ActionType.MOVEMENT
        assert result.modifiers.get("to_zone") == "market_corner"

    def test_movement_chinese_location_alias_resolves(self, world_state: WorldState):
        """P0.3 residual: the LLM puts a Chinese location name ('广场') in
        target_ref and flags it ambiguous. The parser must resolve it to
        town_square instead of bouncing the player with a clarification —
        exercised through the FULL parse() path, not just coherence."""
        provider = FakeLLMProvider("fixtures")
        provider.register_fixture(
            task_type="parse_player_intent",
            prompt="回到广场",
            expected_output={
                "intent_id": "intent_001",
                "source": "natural_language",
                "raw_text": "回到广场",
                "intent_type": "movement",
                "actor_id": "player_001",
                "target_ref": "广场",
                "content": None,
                "commitment": "committed",
                "confidence": 0.8,
                "ambiguities": ["广场"],
                "timestamp": 1,
            },
        )
        parser = IntentParser(llm_orchestrator=LLMOrchestrator(primary_provider=provider))
        result = parser.parse(
            raw_text="回到广场",
            actor_id="player_001",
            tick=1,
            world=world_state,
        )
        assert isinstance(result, ParsedIntent), f"expected ParsedIntent, got {result}"
        assert result.intent_type == ActionType.MOVEMENT
        assert result.modifiers.get("to_location") == "town_square"

    def test_movement_location_substring_resolves(self, world_state: WorldState):
        # A partial english location id ('square') resolves to town_square.
        provider = FakeLLMProvider("fixtures")
        provider.register_fixture(
            task_type="parse_player_intent",
            prompt="去 square",
            expected_output={
                "intent_id": "intent_001",
                "source": "natural_language",
                "raw_text": "去 square",
                "intent_type": "movement",
                "actor_id": "player_001",
                "target_ref": "square",
                "commitment": "committed",
                "confidence": 0.8,
                "ambiguities": ["square"],
                "timestamp": 1,
            },
        )
        parser = IntentParser(llm_orchestrator=LLMOrchestrator(primary_provider=provider))
        result = parser.parse(
            raw_text="去 square", actor_id="player_001", tick=1, world=world_state,
        )
        assert isinstance(result, ParsedIntent)
        assert result.modifiers.get("to_location") == "town_square"

    def test_valid_to_location_clears_stale_ambiguity(self, world_state: WorldState):
        """P1.5: the LLM gives a VALID to_location but also flags it ambiguous
        (e.g. 去铁匠铺 → to_location='blacksmith', ambiguities=['铁匠铺']). The
        valid destination must win — no needless clarification."""
        provider = FakeLLMProvider("fixtures")
        provider.register_fixture(
            task_type="parse_player_intent",
            prompt="去铁匠铺",
            expected_output={
                "intent_id": "intent_001",
                "source": "natural_language",
                "raw_text": "去铁匠铺",
                "intent_type": "movement",
                "actor_id": "player_001",
                "target_ref": "铁匠铺",
                "modifiers": {"to_location": "blacksmith"},  # valid id in world_state
                "commitment": "committed",
                "confidence": 0.8,
                "ambiguities": ["铁匠铺"],
                "timestamp": 1,
            },
        )
        parser = IntentParser(llm_orchestrator=LLMOrchestrator(primary_provider=provider))
        result = parser.parse(
            raw_text="去铁匠铺", actor_id="player_001", tick=1, world=world_state,
        )
        assert isinstance(result, ParsedIntent), f"bounced: {result}"
        assert result.modifiers.get("to_location") == "blacksmith"
        assert result.ambiguities == []

    def test_unresolvable_destination_gives_location_list(self, world_state: WorldState):
        """P1.5: an unknown destination ('市场'/'market' — no such location) must
        return a movement clarification that lists the REAL reachable locations,
        not the useless generic '尝试执行 / 取消动作'."""
        provider = FakeLLMProvider("fixtures")
        provider.register_fixture(
            task_type="parse_player_intent",
            prompt="前往市场",
            expected_output={
                "intent_id": "intent_001",
                "source": "natural_language",
                "raw_text": "前往市场",
                "intent_type": "movement",
                "actor_id": "player_001",
                "target_ref": "市场",
                "modifiers": {"to_location": "market"},  # NOT a real location
                "commitment": "committed",
                "confidence": 0.7,
                "ambiguities": ["市场"],
                "timestamp": 1,
            },
        )
        parser = IntentParser(llm_orchestrator=LLMOrchestrator(primary_provider=provider))
        result = parser.parse(
            raw_text="前往市场", actor_id="player_001", tick=1, world=world_state,
        )
        assert isinstance(result, ClarificationRequest)
        assert result.ambiguity_type == "movement_destination"
        # Options name the real locations, not generic placeholders.
        opts = result.options or []
        assert "town_square" in opts
        assert "尝试执行" not in opts

    def test_prompt_includes_location_list(self, world_state: WorldState):
        """P1.5 root: the parser prompt must list valid locations so the LLM
        stops inventing destinations like 'market'/'blacksmith'."""
        parser = IntentParser(llm_orchestrator=LLMOrchestrator(primary_provider=FakeLLMProvider()))
        ctx = {
            "entities": [],
            "locations": [{"location_id": lid} for lid in world_state.locations],
        }
        prompt = parser._build_prompt("{entities_list}\n{locations_list}", "去某处",
                                       "player_001", ctx)
        assert "town_square" in prompt
        assert "blacksmith" in prompt

    def test_parse_with_coherence_error_returns_clarification(
        self, parser: IntentParser, world_state: WorldState
    ):
        # Register a fixture with a non-existent target
        provider = FakeLLMProvider("fixtures")
        provider.register_fixture(
            task_type="parse_player_intent",
            prompt="对幽灵说话",
            expected_output={
                "intent_id": "intent_001",
                "source": "natural_language",
                "raw_text": "对幽灵说话",
                "intent_type": "speech",
                "actor_id": "player_001",
                "target_id": "npc.ghost",
                "content": "你好",
                "commitment": "committed",
                "confidence": 0.8,
                "timestamp": 1,
            },
        )
        llm = LLMOrchestrator(primary_provider=provider)
        parser2 = IntentParser(llm_orchestrator=llm)

        result = parser2.parse(
            raw_text="对幽灵说话",
            actor_id="player_001",
            tick=1,
            world=world_state,
        )
        assert isinstance(result, ClarificationRequest)
        assert "npc.ghost" in result.clarifying_question or "矛盾" in result.clarifying_question

    def test_parse_ambiguous_returns_clarification(
        self, parser: IntentParser, world_state: WorldState
    ):
        provider = FakeLLMProvider("fixtures")
        provider.register_fixture(
            task_type="parse_player_intent",
            prompt="对他说话",
            expected_output={
                "intent_id": "intent_001",
                "source": "natural_language",
                "raw_text": "对他说话",
                "intent_type": "speech",
                "actor_id": "player_001",
                "target_ref": "他",
                "content": "你好",
                "commitment": "committed",
                "confidence": 0.6,
                "ambiguities": ["'他'指代不明"],
                "timestamp": 1,
            },
        )
        llm = LLMOrchestrator(primary_provider=provider)
        parser2 = IntentParser(llm_orchestrator=llm)

        result = parser2.parse(
            raw_text="对他说话",
            actor_id="player_001",
            tick=1,
            world=world_state,
        )
        assert isinstance(result, ClarificationRequest)
        assert result.ambiguity_type == "pronoun_resolution"
        assert result.options is not None

    def test_parse_llm_failure_returns_clarification(
        self, parser: IntentParser, world_state: WorldState
    ):
        # No fixture registered for this input → LLM call fails
        result = parser.parse(
            raw_text="完全没见过的话",
            actor_id="player_001",
            tick=1,
            world=world_state,
        )
        assert isinstance(result, ClarificationRequest)
        assert result.ambiguity_type == "parse_failed"

    def test_parse_considering_no_world_effect(self, parser: IntentParser, world_state: WorldState):
        result = parser.parse(
            raw_text="我在想是不是该离开这个镇子了",
            actor_id="player_001",
            tick=1,
            world=world_state,
        )
        assert isinstance(result, ParsedIntent)
        assert result.commitment == CommitmentLevel.CONSIDERING
        assert result.performed_content is None  # no observable action
        assert result.player_intent_note == "考虑离开镇子"

    def test_parse_preparing_gets_warning_but_not_blocked(self, parser: IntentParser, world_state: WorldState):
        result = parser.parse(
            raw_text="我准备趁乱偷偷溜到铁匠铺后面",
            actor_id="player_001",
            tick=1,
            world=world_state,
        )
        # preparing + physical triggers a coherence WARNING, not ERROR
        # Warnings do not block execution — only errors return ClarificationRequest
        assert isinstance(result, ParsedIntent)
        assert result.commitment == CommitmentLevel.PREPARING
        assert result.performed_content is None  # preparing = no observable action yet

    def test_parse_attempting_steal(self, parser: IntentParser, world_state: WorldState):
        result = parser.parse(
            raw_text="我试着从展示台上拿走那把短剑",
            actor_id="player_001",
            tick=1,
            world=world_state,
        )
        assert isinstance(result, ParsedIntent)
        assert result.commitment == CommitmentLevel.ATTEMPTING
        assert result.intent_type == ActionType.PHYSICAL
        assert result.performed_content == "从展示台上拿走短剑"

    def test_parse_quick_command(self, parser: IntentParser, world_state: WorldState):
        result = parser.parse(
            raw_text="/examine short_sword",
            actor_id="player_001",
            tick=1,
            world=world_state,
        )
        assert isinstance(result, ParsedIntent)
        assert result.source == "quick_command"
        assert result.intent_type == ActionType.LOOK
        assert result.target_ref == "short_sword"

    def test_parse_ambiguous_pronoun_from_fixture(self, parser: IntentParser, world_state: WorldState):
        result = parser.parse(
            raw_text="对他说话",
            actor_id="player_001",
            tick=1,
            world=world_state,
        )
        assert isinstance(result, ClarificationRequest)
        assert result.ambiguity_type == "pronoun_resolution"
        assert result.options is not None
        assert "他" in result.clarifying_question

    def test_parse_emotional_speech(self, parser: IntentParser, world_state: WorldState):
        result = parser.parse(
            raw_text="我愤怒地对守卫大喊：你们到底在隐瞒什么！",
            actor_id="player_001",
            tick=1,
            world=world_state,
        )
        assert isinstance(result, ParsedIntent)
        assert result.intent_type == ActionType.SPEECH
        assert result.modifiers.get("emotion") == "angry"
        assert result.modifiers.get("volume") == "loud"
        assert result.target_id == "npc.guard_b"

    def test_parse_empty_input(self, parser: IntentParser, world_state: WorldState):
        result = parser.parse(
            raw_text="",
            actor_id="player_001",
            tick=1,
            world=world_state,
        )
        assert isinstance(result, ClarificationRequest)
        assert result.ambiguity_type == "parse_failed"


def test_match_location_resolves_display_name_not_just_id():
    """Playability audit #3: the player types a place's DISPLAY NAME (征船听证台 /
    听证台), never its internal id (pump_gate) — _match_location must resolve both,
    so a movement isn't bounced to a raw-id menu."""
    world = WorldState(locations={
        "pump_gate": LocationState(location_id="pump_gate", name="征船听证台"),
        "pump_house": LocationState(location_id="pump_house", name="三号净水泵房"),
    })
    assert IntentParser._match_location("征船听证台", world) == "pump_gate"  # exact name
    assert IntentParser._match_location("听证台", world) == "pump_gate"      # name substring
    assert IntentParser._match_location("pump_gate", world) == "pump_gate"   # id still works
    assert IntentParser._match_location("pump", world) is None              # ambiguous → no false hit


def test_scan_raw_for_location_finds_embedded_name():
    """Audit F3: a location named verbatim in a longer sentence the LLM didn't
    extract cleanly is found, so movement isn't bounced to a menu."""
    world = WorldState(locations={
        "pump_gate": LocationState(location_id="pump_gate", name="征船听证台"),
        "pump_house": LocationState(location_id="pump_house", name="三号净水泵房"),
    })
    assert IntentParser._scan_raw_for_location("我返回征船听证台找林槐", world) == "pump_gate"
    assert IntentParser._scan_raw_for_location("随便走走", world) is None          # no name present
    # two names present → ambiguous → no scan match (stays a clarification)
    assert IntentParser._scan_raw_for_location("从征船听证台去三号净水泵房", world) is None


def _spk(target_id, actor="player_001"):
    return ParsedIntent(
        intent_id="i", source="natural_language", raw_text="", intent_type=ActionType.SPEECH,
        actor_id=actor, target_id=target_id, commitment=CommitmentLevel.COMMITTED,
        confidence=0.9, timestamp=1)


def _world_two_npcs():
    return WorldState(
        entities={
            "player_001": EntityState(entity_id="player_001", entity_type="player", location_id="shrine"),
            "npc.priest": EntityState(entity_id="npc.priest", entity_type="npc", location_id="shrine", name="祭主姞"),
            "npc.assessor": EntityState(entity_id="npc.assessor", entity_type="npc", location_id="court", name="征瓷使严"),
        },
        locations={"shrine": LocationState(location_id="shrine"), "court": LocationState(location_id="court")},
    )


def test_absent_speech_target_retargets_to_present_partner():
    """Grand-integration: a SPEECH the LLM bound to an ABSENT NPC (because the
    sentence mentioned that NPC's domain) retargets to the present addressee instead
    of dead-ending the turn."""
    w = _world_two_npcs()
    conv = {"participants": ["player_001", "npc.priest"]}
    # bound to absent 征瓷使, but the player is mid-conversation with present 祭主姞 and
    # didn't name 征瓷使 — retarget to 祭主姞
    out = IntentParser._retarget_absent_speech_to_present_partner(
        _spk("npc.assessor"), w, conv, "祭主，关于定罪这桩冤案我想替掘泥户申诉")
    assert out.target_id == "npc.priest"


def test_absent_speech_kept_when_named_verbatim():
    w = _world_two_npcs()
    out = IntentParser._retarget_absent_speech_to_present_partner(
        _spk("npc.assessor"), w, {"participants": ["player_001", "npc.priest"]},
        "请祭主转告征瓷使严，掘泥户是冤枉的")              # names 征瓷使严 → explicit cross-location
    assert out.target_id == "npc.assessor"


def test_present_speech_target_unchanged():
    w = _world_two_npcs()
    out = IntentParser._retarget_absent_speech_to_present_partner(
        _spk("npc.priest"), w, None, "祭主，请受理这桩申诉")
    assert out.target_id == "npc.priest"
