"""Playtest bug: a compound "走到X面前问他：Y" gets classified by the LLM as a
bare MOVEMENT, which drops the question AND relocates the player (stranding them
away from the NPC they wanted to talk to). A deterministic post-parse guard
reclassifies a movement that carries speech content aimed at an NPC as SPEECH.
"""
from __future__ import annotations

from verisaria.runtime.session import GameSession
from verisaria.engine.schemas import ActionType, CommitmentLevel, ParsedIntent

PACK = "fixtures/content_packs/frostgate_watchpost.json"
PLAYER = "player_001"
BRANN = "npc.captain_brann"


def _movement_with_speech(raw: str) -> ParsedIntent:
    # Mimic the real parser's mistake on "我走到队长布兰面前问他：…".
    return ParsedIntent(
        intent_id="i", source="natural_language", raw_text=raw,
        intent_type=ActionType.MOVEMENT, actor_id=PLAYER, target_id=BRANN,
        content="难民入营这事，到底卡在哪儿？", modifiers={"to_location": "barracks"},
        commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content=raw, timestamp=0,
    )


def test_approach_and_ask_does_not_relocate_player(tmp_path):
    s = GameSession(PACK, save_dir=str(tmp_path))
    s.intent_parser.parse = lambda raw_text, **kw: _movement_with_speech(raw_text)
    before = s.world.state.get_entity(PLAYER).location_id

    out = s.run_tick("我走到队长布兰面前问他：难民入营这事，到底卡在哪儿？")

    # The player stays put (not stranded) and the turn isn't a bare player move.
    assert s.world.state.get_entity(PLAYER).location_id == before
    assert "你改变了位置" not in out  # player-specific (NPCs may move on their own)


def test_approach_and_ask_reaches_the_npc_as_speech(tmp_path):
    s = GameSession(PACK, save_dir=str(tmp_path))
    s.intent_parser.parse = lambda raw_text, **kw: _movement_with_speech(raw_text)

    s.run_tick("我走到队长布兰面前问他：难民入营这事，到底卡在哪儿？")

    # Brann heard the player speak (the question was delivered, not dropped).
    heard = [m.content for m in s.memory_store.get_all(BRANN)]
    assert any("难民入营这事" in c or "听到" in c for c in heard)


def test_addressed_npc_is_engaged_even_without_a_session(tmp_path):
    """Robustness: the NPC the player addresses is treated as in-conversation
    this tick regardless of session bookkeeping, so it replies instead of idly
    wandering off (real-play bug: the addressed captain walked to the barracks)."""
    s = GameSession(PACK, save_dir=str(tmp_path))
    seen: dict = {}
    orig = s.npc_action_generator.generate_actions

    def ga(*a, **kw):
        seen["active"] = set(kw.get("active_conversation_entity_ids") or [])
        return orig(*a, **kw)

    s.npc_action_generator.generate_actions = ga
    # No conversation session exists; collect directly with an addressed NPC.
    s._collect_npc_actions(suppress_idle_speech_at="gatehouse", addressed_npc=BRANN)
    assert BRANN in seen["active"]


def test_pure_movement_without_speech_still_moves(tmp_path):
    """A genuine move (no speech content) is left as movement."""
    s = GameSession(PACK, save_dir=str(tmp_path))

    def pure_move(raw_text, **kw):
        return ParsedIntent(
            intent_id="i", source="natural_language", raw_text=raw_text,
            intent_type=ActionType.MOVEMENT, actor_id=PLAYER, target_id=None,
            content=None, modifiers={"to_location": "barracks"},
            commitment=CommitmentLevel.COMMITTED, confidence=0.9,
            performed_content=raw_text, timestamp=0,
        )

    s.intent_parser.parse = pure_move
    s.run_tick("去兵营")
    assert s.world.state.get_entity(PLAYER).location_id == "barracks"


def test_lingering_conversation_npc_is_not_forced_to_speak(tmp_path):
    """Over-chatter bug (live in the TUI): an NPC the player addressed turns ago kept
    a lingering active session, so it counted as 'in conversation' and interjected
    EVERY tick (voss kept repeating "我信…"). The auto-reply set is now the addressed
    NPC's current conversation only — a lingering / NPC-NPC session no longer forces
    its members to speak."""
    s = GameSession(PACK, save_dir=str(tmp_path))
    s.conversation_manager.start_session(
        initiator_id=PLAYER, participants=["npc.sentry_voss"], tick=0
    )  # voss: a lingering session from an earlier exchange
    seen: dict = {}
    orig = s.npc_action_generator.generate_actions

    def spy(*a, **kw):
        seen["speak"] = set(kw.get("active_conversation_entity_ids") or [])
        return orig(*a, **kw)

    s.npc_action_generator.generate_actions = spy
    s._collect_npc_actions(suppress_idle_speech_at="gatehouse", addressed_npc=BRANN)

    assert BRANN in seen["speak"]                  # addressed → auto-replies
    assert "npc.sentry_voss" not in seen["speak"]  # lingering → not forced to speak
