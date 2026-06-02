"""Tests for multi-turn clarification flow in GameSession.

Verifies that ambiguous player input triggers a clarification exchange
rather than failing immediately, and that the player can resolve
ambiguity via option selection or free-text follow-up.
"""

from __future__ import annotations

import pytest

from verisaria.runtime.session import GameSession, ClarificationContext
from verisaria.engine.intent import ClarificationRequest, IntentParser
from verisaria.engine.llm import FakeLLMProvider, LLMOrchestrator
from verisaria.engine.schemas import ActionType, CommitmentLevel, ParsedIntent
from verisaria.engine.world import EntityState, WorldState


@pytest.fixture
def minimal_pack_path(tmp_path_factory):
    return "fixtures/content_packs/minimal_valid.json"


def _make_parsed_intent(
    raw_text: str = "look around",
    intent_type: ActionType = ActionType.LOOK,
    commitment: CommitmentLevel = CommitmentLevel.COMMITTED,
    ambiguities: list[str] | None = None,
) -> ParsedIntent:
    return ParsedIntent(
        intent_id="intent_001",
        source="natural_language",
        raw_text=raw_text,
        intent_type=intent_type,
        actor_id="player_001",
        commitment=commitment,
        confidence=0.9,
        ambiguities=ambiguities or [],
        performed_content=raw_text,
        timestamp=0,
    )


def _make_clarification_request(
    raw_text: str = "打他",
    question: str = "你提到的'他'是指谁？",
    options: list[str] | None = None,
) -> ClarificationRequest:
    return ClarificationRequest(
        request_id="clarify_001",
        original_input=raw_text,
        clarifying_question=question,
        options=options or ["附近的人", "自己", "取消动作"],
        ambiguity_type="pronoun_resolution",
    )


class TestClarificationFlow:
    """End-to-end clarification scenarios."""

    def test_single_turn_clarification_with_option(self, minimal_pack_path, tmp_path):
        """Player selects a numeric option to resolve ambiguity."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        call_count = 0

        def mock_parse(raw_text, actor_id, tick, world, context=None, skip_ambiguity_check=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_clarification_request(raw_text="打他")
            # Second call — after player selects option 1
            assert "附近的人" in raw_text
            return _make_parsed_intent(raw_text=raw_text, intent_type=ActionType.COMBAT)

        session.intent_parser.parse = mock_parse

        # First tick: ambiguous input → clarification question
        result1 = session.run_tick("打他")
        assert "是指谁" in result1
        assert session._active_clarification is not None
        assert session._active_clarification.round == 1

        # Second tick: player selects option 1
        result2 = session.run_tick("1")
        assert session._active_clarification is None
        assert call_count == 2

    def test_multi_turn_clarification(self, minimal_pack_path, tmp_path):
        """Ambiguity persists for two rounds before resolution."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        call_count = 0

        def mock_parse(raw_text, actor_id, tick, world, context=None, skip_ambiguity_check=False):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return _make_clarification_request(raw_text=raw_text)
            return _make_parsed_intent(raw_text=raw_text)

        session.intent_parser.parse = mock_parse

        result1 = session.run_tick("打他")
        assert session._active_clarification is not None
        result2 = session.run_tick("附近的人")
        assert session._active_clarification is not None
        assert session._active_clarification.round == 2
        result3 = session.run_tick("1")
        assert session._active_clarification is None
        assert call_count == 3

    def test_max_rounds_exceeded(self, minimal_pack_path, tmp_path):
        """After MAX_CLARIFICATION_DEPTH rounds, give up gracefully."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))

        def mock_parse(raw_text, actor_id, tick, world, context=None, skip_ambiguity_check=False):
            return _make_clarification_request(raw_text=raw_text)

        session.intent_parser.parse = mock_parse

        # Round 1
        session.run_tick("打他")
        assert session._active_clarification is not None

        # Round 2
        session.run_tick("1")
        assert session._active_clarification is not None
        assert session._active_clarification.round == 2

        # Round 3
        session.run_tick("1")
        assert session._active_clarification is not None
        assert session._active_clarification.round == 3

        # Round 4 — exceeds MAX_CLARIFICATION_DEPTH (3)
        result = session.run_tick("1")
        assert session._active_clarification is None
        assert "多次澄清后仍无法理解" in result

    def test_cancel_with_slash_command(self, minimal_pack_path, tmp_path):
        """Player can cancel an active clarification with /cancel."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        session.intent_parser.parse = lambda **kw: _make_clarification_request()

        session.run_tick("打他")
        assert session._active_clarification is not None

        result = session.run_tick("/cancel")
        assert session._active_clarification is None
        assert "已取消" in result

    def test_cancel_with_chinese_text(self, minimal_pack_path, tmp_path):
        """Player can cancel by typing '取消'."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        session.intent_parser.parse = lambda **kw: _make_clarification_request()

        session.run_tick("打他")
        result = session.run_tick("取消")
        assert session._active_clarification is None
        assert "已取消" in result

    def test_cancel_option_selection(self, minimal_pack_path, tmp_path):
        """Selecting the '取消动作' option cancels the action."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        session.intent_parser.parse = lambda **kw: _make_clarification_request(
            options=["选项A", "取消动作"]
        )

        session.run_tick("打他")
        result = session.run_tick("2")
        assert session._active_clarification is None
        assert "已取消" in result

    def test_empty_response_reprompts(self, minimal_pack_path, tmp_path):
        """Empty reply during clarification re-shows the question."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        req = _make_clarification_request()
        session.intent_parser.parse = lambda **kw: req

        session.run_tick("打他")
        result = session.run_tick("")
        assert "请提供更多信息" in result
        assert session._active_clarification is not None

    def test_free_text_clarification(self, minimal_pack_path, tmp_path):
        """Player can resolve ambiguity with free-text follow-up."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        call_count = 0

        def mock_parse(raw_text, actor_id, tick, world, context=None, skip_ambiguity_check=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_clarification_request()
            assert "具体来说是" in raw_text
            return _make_parsed_intent(raw_text=raw_text)

        session.intent_parser.parse = mock_parse

        session.run_tick("打他")
        result = session.run_tick("门口那个戴帽子的人")
        assert session._active_clarification is None
        assert call_count == 2

    def test_parse_failed_does_not_enter_merge_loop(self, minimal_pack_path, tmp_path):
        """PLAY-2: when the LLM can't parse the input at all (parse_failed), the
        next input must be a FRESH attempt, not merged with the unparseable
        original (which produced garbled '...具体来说是：...' sent to the wrong NPC)."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        seen_raw: list[str] = []

        def mock_parse(raw_text, actor_id, tick, world, context=None, skip_ambiguity_check=False):
            seen_raw.append(raw_text)
            if raw_text == "问布兰队长难民为什么不能进来":
                return ClarificationRequest(
                    request_id="c", original_input=raw_text,
                    clarifying_question="我没理解你的意思，能再描述一下吗？",
                    ambiguity_type="parse_failed",
                )
            return _make_parsed_intent(raw_text=raw_text, intent_type=ActionType.SPEECH)

        session.intent_parser.parse = mock_parse

        out1 = session.run_tick("问布兰队长难民为什么不能进来")
        assert "没理解" in out1
        # A parse failure must NOT leave a sticky clarification waiting to merge.
        assert session._active_clarification is None

        # The next input is a brand-new question — it must reach parse() verbatim,
        # never as "原句，具体来说是：新句".
        session.run_tick("问哨兵伏斯他怕什么")
        assert "问哨兵伏斯他怕什么" in seen_raw
        assert not any("具体来说是" in r for r in seen_raw)

    def test_fresh_command_after_ignored_clarification_runs_clean(
        self, minimal_pack_path, tmp_path
    ):
        """Hard-playtest bug B1: a pending clarification ('传令兵' 指代不明) glued the
        NEXT, unrelated command ('求你开城门…') onto the stale referent, parsing it
        as ref='那个不存在的传令兵'. A self-contained new command must run FRESH —
        never as '原句，具体来说是：新句'."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        seen: list[str] = []

        def mock_parse(raw_text, actor_id, tick, world, context=None, skip_ambiguity_check=False):
            seen.append(raw_text)
            if "传令兵" in raw_text:
                return ClarificationRequest(
                    request_id="c", original_input=raw_text,
                    clarifying_question="'传令兵' 指代不明，请确认：",
                    options=["尝试执行", "取消动作"],
                    ambiguity_type="ambiguous_reference",
                )
            return _make_parsed_intent(raw_text=raw_text, intent_type=ActionType.SPEECH)

        session.intent_parser.parse = mock_parse

        session.run_tick("跟那个不存在的传令兵说句话。")
        assert session._active_clarification is not None

        session.run_tick("队长，我以性命和荣誉担保，求你开城门、接纳这些难民吧！")
        # The new command reached parse() verbatim, was not merged, and cleared
        # the stale clarification.
        assert "队长，我以性命和荣誉担保，求你开城门、接纳这些难民吧！" in seen
        assert not any("具体来说是" in r for r in seen)
        assert not any("传令兵" in r and "开城门" in r for r in seen)
        assert session._active_clarification is None

    def test_short_referent_answer_still_merges(self, minimal_pack_path, tmp_path):
        """Guard against over-eager freshness: a bare noun-phrase disambiguation
        (no sentence-final punctuation, no addressing verb) is STILL a clarification
        answer and merges into the original input."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        assert session._looks_like_fresh_command("门口那个戴帽子的人") is False
        assert session._looks_like_fresh_command("卡泽") is False
        assert session._looks_like_fresh_command("hello") is False
        # Full self-contained utterances are fresh commands.
        assert session._looks_like_fresh_command("求你开城门、接纳难民吧！") is True
        assert session._looks_like_fresh_command("对队长布兰说：你怕什么？") is True
        assert session._looks_like_fresh_command("/who") is True

    def test_format_clarification_question_with_options(self):
        """Question formatting includes numbered options and hint."""
        req = ClarificationRequest(
            request_id="c1",
            original_input="test",
            clarifying_question="Which one?",
            options=["A", "B"],
        )
        text = GameSession._format_clarification_question(req)
        assert "Which one?" in text
        assert "1. A" in text
        assert "2. B" in text
        assert "/cancel" in text

    def test_format_clarification_question_without_options(self):
        """Question formatting without options still shows /cancel hint."""
        req = ClarificationRequest(
            request_id="c1",
            original_input="test",
            clarifying_question="Say more?",
            options=None,
        )
        text = GameSession._format_clarification_question(req)
        assert "Say more?" in text
        assert "/cancel" in text
        assert "1." not in text

    def test_resolve_clarification_numeric_option(self, minimal_pack_path, tmp_path):
        """_resolve_clarification correctly maps option numbers."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        ctx = ClarificationContext(
            original_input="打他",
            options=["附近的人", "自己"],
            round=1,
        )
        result, skip = session._resolve_clarification(ctx, "1")
        assert "指附近的人" in result
        assert "打他" in result
        assert skip is True  # explicit choice → skip ambiguity check

    def test_resolve_clarification_out_of_range_number(self, minimal_pack_path, tmp_path):
        """Invalid option number falls back to free-text append."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        ctx = ClarificationContext(
            original_input="打他",
            options=["附近的人"],
            round=1,
        )
        result, skip = session._resolve_clarification(ctx, "99")
        assert "具体来说是：99" in result
        assert skip is True  # explicit text → skip ambiguity check

    def test_resolve_clarification_no_options(self, minimal_pack_path, tmp_path):
        """When no options are provided, response is always free-text."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        ctx = ClarificationContext(
            original_input="打他",
            options=[],
            round=1,
        )
        result, skip = session._resolve_clarification(ctx, "hello")
        assert "具体来说是：hello" in result
        assert skip is True  # explicit text → skip ambiguity check

    def test_numeric_option_skips_ambiguity_check(self, minimal_pack_path, tmp_path):
        """When player selects a concrete option, skip_ambiguity_check=True
        is passed to parse() so the same ambiguity does not re-trigger."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        call_log: list[dict] = []

        def mock_parse(raw_text, actor_id, tick, world, context=None, skip_ambiguity_check=False):
            call_log.append({
                "raw_text": raw_text,
                "skip_ambiguity_check": skip_ambiguity_check,
            })
            if not skip_ambiguity_check:
                return _make_clarification_request(raw_text="追问她")
            return _make_parsed_intent(raw_text=raw_text, intent_type=ActionType.SPEECH)

        session.intent_parser.parse = mock_parse

        # First tick triggers clarification
        session.run_tick("追问她")
        assert session._active_clarification is not None
        assert call_log[-1]["skip_ambiguity_check"] is False

        # Second tick: player selects option 1 → skip_ambiguity_check must be True
        session.run_tick("1")
        assert session._active_clarification is None
        assert call_log[-1]["skip_ambiguity_check"] is True
        assert "附近的人" in call_log[-1]["raw_text"]


class TestConversationAwarePronounResolution:
    """End-to-end tests for pronoun resolution using conversation context."""

    def test_pronoun_resolved_from_conversation_context(self, minimal_pack_path, tmp_path):
        """When player is in conversation with npc.ele, '她' resolves to npc.ele."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))

        # Add npc.ele to the world so the conversation partner exists
        session.world.state.entities["npc.ele"] = EntityState(
            entity_id="npc.ele",
            entity_type="npc",
            location_id="void",
        )

        # Start a conversation between player and npc.ele
        session.conversation_manager.start_session(
            initiator_id=session.player_id,
            participants=["npc.ele"],
            tick=session.world.state.tick,
        )

        # Mock the intent parser to return a ParsedIntent with target_ref='她'
        # but NO target_id set (simulating LLM returning ambiguous pronoun)
        def mock_parse(raw_text, actor_id, tick, world, context=None, skip_ambiguity_check=False):
            # The _resolve_target_ref in IntentParser should resolve '她' to npc.ele
            # via active_conversation context. We simulate the LLM returning
            # an intent with target_ref='她' and no target_id.
            return ParsedIntent(
                intent_id="intent_001",
                source="natural_language",
                raw_text=raw_text,
                intent_type=ActionType.SPEECH,
                actor_id=actor_id,
                target_ref="她",
                target_id=None,  # Not set by LLM
                content="最近怎么样",
                commitment=CommitmentLevel.COMMITTED,
                confidence=0.9,
                ambiguities=["她"],  # Ambiguity reported by LLM
                performed_content=raw_text,
                timestamp=tick,
            )

        session.intent_parser.parse = mock_parse

        # Action: player says "追问她"
        result = session.run_tick("追问她")

        # Assert: NO ClarificationRequest is returned
        assert "是指谁" not in result, f"Expected pronoun to be resolved, but got clarification: {result}"
        assert session._active_clarification is None, "Expected no active clarification"

        # Verify the intent was processed (result should be narrative, not a question)
        assert result != "多次澄清后仍无法理解，请换一种方式描述。"

    def test_clarification_options_show_conversation_partner(self, minimal_pack_path, tmp_path):
        """When player is in conversation, _build_clarification options show partner name."""
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))

        # Build an intent with ambiguity for "她"
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="追问她最近怎么样",
            intent_type=ActionType.SPEECH,
            actor_id="player_001",
            target_ref="她",
            target_id=None,
            content="最近怎么样",
            commitment=CommitmentLevel.COMMITTED,
            confidence=0.7,
            ambiguities=["她"],
            performed_content="追问她最近怎么样",
            timestamp=1,
        )

        # Simulate active conversation context with npc.ele as partner
        active_conversation = {
            "session_id": "conv_1_1",
            "participants": ["player_001", "npc.ele"],
            "other_participants": ["npc.ele"],
            "last_speaker": "npc.ele",
            "last_content": "你好",
        }

        # Call _build_clarification directly
        req = session.intent_parser._build_clarification(
            request_id="clarify_001",
            raw_text="追问她最近怎么样",
            intent=intent,
            world=session.world.state,
            active_conversation=active_conversation,
        )

        # Assert: returned a ClarificationRequest
        assert isinstance(req, ClarificationRequest), f"Expected ClarificationRequest, got: {type(req)}"

        # Assert: options contain the conversation partner's name
        assert any("npc.ele" in opt for opt in req.options), (
            f"Expected options to contain 'npc.ele', got: {req.options}"
        )

        # Assert: options do NOT contain generic "附近的人"
        assert "附近的人" not in req.options, (
            f"Expected options NOT to contain generic '附近的人', got: {req.options}"
        )


def _bare_parser() -> IntentParser:
    """An IntentParser whose LLM is never actually called (callers drive the
    resolution helpers directly)."""
    return IntentParser(LLMOrchestrator(primary_provider=FakeLLMProvider()))


def _world_with_npcs(npc_ids: list[str], loc: str = "town_square") -> WorldState:
    """A minimal world: player_001 plus the given NPCs, all co-located."""
    entities = {
        "player_001": EntityState(
            entity_id="player_001", entity_type="player", location_id=loc
        )
    }
    for nid in npc_ids:
        entities[nid] = EntityState(entity_id=nid, entity_type="npc", location_id=loc)
    return WorldState(tick=1, entities=entities)


def _pronoun_intent(pronoun: str = "他") -> ParsedIntent:
    return ParsedIntent(
        intent_id="intent_001",
        source="natural_language",
        raw_text=f"追问{pronoun}",
        intent_type=ActionType.SPEECH,
        actor_id="player_001",
        target_ref=pronoun,
        target_id=None,
        content="最近怎么样",
        commitment=CommitmentLevel.COMMITTED,
        confidence=0.7,
        ambiguities=[pronoun],
        performed_content=f"追问{pronoun}",
        timestamp=1,
    )


class TestPronounCandidateResolution:
    """P0.2: pronoun clarification draws candidates from world state and
    auto-resolves when exactly one plausible target exists."""

    def test_auto_resolves_pronoun_to_single_nearby_npc(self):
        parser = _bare_parser()
        world = _world_with_npcs(["npc.ele"])
        intent = _pronoun_intent("他")

        resolved = parser._auto_resolve_single_candidate(intent, world, None)

        assert resolved.target_id == "npc.ele"
        assert resolved.ambiguities == []

    def test_no_auto_resolve_when_multiple_nearby(self):
        parser = _bare_parser()
        world = _world_with_npcs(["npc.ele", "npc.guard_b"])
        intent = _pronoun_intent("他")

        resolved = parser._auto_resolve_single_candidate(intent, world, None)

        assert resolved.target_id is None
        assert "他" in resolved.ambiguities

    def test_no_auto_resolve_when_nobody_nearby(self):
        parser = _bare_parser()
        world = _world_with_npcs([])  # player is alone
        intent = _pronoun_intent("她")

        resolved = parser._auto_resolve_single_candidate(intent, world, None)

        assert resolved.target_id is None
        assert "她" in resolved.ambiguities

    def test_clarification_options_list_same_location_npcs(self):
        parser = _bare_parser()
        world = _world_with_npcs(["npc.ele", "npc.guard_b"])
        intent = _pronoun_intent("他")

        req = parser._build_clarification("clarify_001", "追问他", intent, world, None)

        assert isinstance(req, ClarificationRequest)
        assert "npc.ele" in req.options
        assert "npc.guard_b" in req.options
        # P0.2: no more useless generic placeholders
        assert "附近的人" not in req.options
        assert "自己" not in req.options

    def test_single_candidate_clarification_is_a_confirm(self):
        parser = _bare_parser()
        world = _world_with_npcs(["npc.ele"])
        intent = _pronoun_intent("他")

        req = parser._build_clarification("clarify_001", "追问他", intent, world, None)

        assert req.options == ["npc.ele", "取消动作"]
        assert "npc.ele" in req.clarifying_question

    def test_parse_auto_resolves_single_nearby_without_clarification(self):
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
                "ambiguities": ["他"],
                "timestamp": 1,
            },
        )
        parser = IntentParser(LLMOrchestrator(primary_provider=provider))
        world = _world_with_npcs(["npc.ele"])

        result = parser.parse(
            raw_text="对他说话",
            actor_id="player_001",
            tick=1,
            world=world,
        )

        # Exactly one nearby actor -> resolve directly, never ask.
        assert isinstance(result, ParsedIntent)
        assert result.target_id == "npc.ele"
