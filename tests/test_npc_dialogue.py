"""Tests for P1.1: LLM-backed NPC dialogue with deterministic template fallback.

Covers the standalone NPCDialogueGenerator (prompt grounding + LLM round-trip)
and its integration into NPCActionGenerator (LLM-first, template-fallback).
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from verisaria.engine.llm import FakeLLMProvider, LLMOrchestrator
from verisaria.engine.npc_dialogue import NPCDialogue, NPCDialogueGenerator
from verisaria.engine.npc_runtime import NPCActionGenerator
from verisaria.engine.schemas import ActionType
from verisaria.engine.world import EntityState, WorldState


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #

class _FakeDialogueGen:
    """Stand-in dialogue generator that returns a fixed line (or None)."""

    def __init__(self, line: str | None) -> None:
        self.line = line
        self.calls: list[str] = []

    def generate_line(self, npc_id, entity=None, world=None, memory_store=None,
                      conversation_session=None):
        self.calls.append(npc_id)
        return self.line


class _StubMemoryStore:
    def __init__(self, mems: list[tuple[str, float]]) -> None:
        self._mems = [SimpleNamespace(content=c, salience=s) for c, s in mems]

    def get_all(self, owner_id):
        return list(self._mems)


class _StubConvMgr:
    def __init__(self, session=None) -> None:
        self._session = session

    def get_active_session(self, actor_id):
        return self._session


def _world() -> WorldState:
    return WorldState(
        tick=1,
        entities={
            "player_001": EntityState(
                entity_id="player_001", entity_type="player", location_id="sq"
            ),
            "npc.ele": EntityState(
                entity_id="npc.ele", entity_type="npc", location_id="sq"
            ),
        },
    )


def _llm_with_line(line: str) -> LLMOrchestrator:
    provider = FakeLLMProvider()
    provider.register_fixture(
        task_type=NPCDialogueGenerator.TASK_TYPE,
        prompt="__npc_dialogue__",
        expected_output={"line": line},
    )
    return LLMOrchestrator(primary_provider=provider)


# --------------------------------------------------------------------------- #
# NPCDialogueGenerator (unit)
# --------------------------------------------------------------------------- #

class TestNPCDialogueGenerator:
    def test_returns_llm_line_with_fixture(self):
        gen = NPCDialogueGenerator(_llm_with_line("（艾蕾压低声音）他们在找你。"))
        line = gen.generate_line("npc.ele")
        assert line == "（艾蕾压低声音）他们在找你。"

    def test_returns_none_when_no_fixture(self):
        gen = NPCDialogueGenerator(LLMOrchestrator(primary_provider=FakeLLMProvider()))
        assert gen.generate_line("npc.ele") is None

    def test_returns_none_on_schema_validation_failure(self):
        provider = FakeLLMProvider()
        provider.register_fixture(
            task_type=NPCDialogueGenerator.TASK_TYPE,
            prompt="__bad__",
            expected_output={"not_line": 1},  # missing required `line`
        )
        gen = NPCDialogueGenerator(LLMOrchestrator(primary_provider=provider))
        assert gen.generate_line("npc.ele") is None

    def test_blank_line_treated_as_none(self):
        gen = NPCDialogueGenerator(_llm_with_line("   "))
        assert gen.generate_line("npc.ele") is None

    def test_prompt_grounds_in_own_memory_and_persona(self):
        gen = NPCDialogueGenerator(LLMOrchestrator(primary_provider=FakeLLMProvider()))
        mem = _StubMemoryStore([("玩家上次帮我赶走了小偷", 0.9), ("天气不错", 0.1)])
        entity = SimpleNamespace(traits=["谨慎", "话少"])

        prompt = gen._build_prompt("npc.ele", entity, mem, None)

        assert "玩家上次帮我赶走了小偷" in prompt   # its own memory
        assert "谨慎" in prompt                      # persona trait
        assert "ele" in prompt                       # who it is
        # A5: we never fed canonical world truth, so it must not appear
        assert "恶魔确实存在" not in prompt

    def test_prompt_includes_conversation_partner_utterance(self):
        gen = NPCDialogueGenerator(LLMOrchestrator(primary_provider=FakeLLMProvider()))
        session = SimpleNamespace(
            topic_stack=["escape_plan"],
            shared_context={"last_speaker": "player_001", "last_content": "我们快走"},
        )
        prompt = gen._build_prompt("npc.ele", None, None, session)
        assert "我们快走" in prompt
        assert "escape_plan" in prompt


# --------------------------------------------------------------------------- #
# NPCActionGenerator integration
# --------------------------------------------------------------------------- #

class TestNPCActionGeneratorDialogue:
    def _speech_content(self, actions):
        speeches = [a for a in actions if a.action_type == ActionType.SPEECH]
        assert speeches, "expected a speech action"
        return speeches[0].params.get("content")

    def test_speech_uses_dialogue_generator(self):
        fake_gen = _FakeDialogueGen("这是LLM生成的台词")
        npc_gen = NPCActionGenerator(seed=42, dialogue_generator=fake_gen)

        actions = npc_gen.generate_actions(
            world=_world(),
            tick=1,
            active_conversation_entity_ids={"npc.ele"},
            memory_store=None,
            conversation_manager=_StubConvMgr(None),
        )

        assert self._speech_content(actions) == "这是LLM生成的台词"
        assert "npc.ele" in fake_gen.calls

    def test_falls_back_to_template_when_generator_returns_none(self):
        fake_gen = _FakeDialogueGen(None)
        npc_gen = NPCActionGenerator(seed=42, dialogue_generator=fake_gen)

        actions = npc_gen.generate_actions(
            world=_world(),
            tick=1,
            active_conversation_entity_ids={"npc.ele"},
            memory_store=None,
            conversation_manager=_StubConvMgr(None),
        )

        content = self._speech_content(actions)
        assert content in NPCActionGenerator.CHATTER_LINES
        assert "npc.ele" in fake_gen.calls

    def test_no_generator_is_backward_compatible(self):
        npc_gen = NPCActionGenerator(seed=42)  # no dialogue_generator

        actions = npc_gen.generate_actions(
            world=_world(),
            tick=1,
            active_conversation_entity_ids={"npc.ele"},
            memory_store=None,
            conversation_manager=_StubConvMgr(None),
        )

        content = self._speech_content(actions)
        assert isinstance(content, str) and content

    def test_deterministic_with_same_seed(self):
        def run():
            npc_gen = NPCActionGenerator(seed=42, dialogue_generator=_FakeDialogueGen("X"))
            return [
                (a.actor_id, a.action_type, a.params)
                for a in npc_gen.generate_actions(
                    world=_world(),
                    tick=1,
                    active_conversation_entity_ids={"npc.ele"},
                    memory_store=None,
                    conversation_manager=_StubConvMgr(None),
                )
            ]

        assert run() == run()

    def test_empty_llm_line_never_yields_empty_speech(self):
        """gpt-oss intermittently returns {"line":""}. A real NPCDialogueGenerator
        wired to such an LLM must still produce a non-empty NPC utterance via the
        template fallback — never an empty speech line."""
        provider = FakeLLMProvider()
        provider.register_fixture(
            task_type=NPCDialogueGenerator.TASK_TYPE,
            prompt="__any_dialogue_prompt__",
            expected_output={"line": ""},  # the problematic gpt-oss output
        )
        gen = NPCDialogueGenerator(LLMOrchestrator(primary_provider=provider))
        npc_gen = NPCActionGenerator(seed=42, dialogue_generator=gen)

        session = SimpleNamespace(
            topic_stack=["镇上传闻"],
            shared_context={"last_speaker": "player_001", "last_content": "最近镇上怎么样？"},
        )
        actions = npc_gen.generate_actions(
            world=_world(),
            tick=1,
            active_conversation_entity_ids={"npc.ele"},
            memory_store=None,
            conversation_manager=_StubConvMgr(session),
        )

        content = self._speech_content(actions)
        assert content, f"NPC produced an empty utterance: {content!r}"
        assert content.strip()


# --------------------------------------------------------------------------- #
# End-to-end GameSession pipeline (deterministic FakeLLM backend)
# --------------------------------------------------------------------------- #

class TestDialogueEndToEnd:
    """Full GameSession.run_tick path: an NPC in conversation must emit a
    non-empty speech Event, even when the LLM yields nothing usable."""

    def _session(self, tmp_path):
        from verisaria.runtime.session import GameSession
        return GameSession(
            "fixtures/content_packs/valid_frontier_town.json",
            save_dir=str(tmp_path),
            llm_backend="fake",
        )

    def test_npc_speech_events_are_never_empty(self, tmp_path):
        session = self._session(tmp_path)
        w = session.world.state
        pid = session.player_id
        ploc = w.entities[pid].location_id

        # Co-locate every NPC with the player and open a conversation each.
        npc_ids = [eid for eid, e in w.entities.items() if e.entity_type == "npc"]
        for nid in npc_ids:
            w.entities[nid].location_id = ploc
            session.conversation_manager.start_session(
                initiator_id=pid, participants=[nid], tick=w.tick
            )
        for nid in npc_ids:
            sess = session.conversation_manager.get_active_session(nid)
            if sess is not None:
                sess.shared_context["last_speaker"] = pid
                sess.shared_context["last_content"] = "最近镇上怎么样？"

        npc_actions = session._collect_npc_actions()
        speeches = [a for a in npc_actions if a.action_type == ActionType.SPEECH]
        assert speeches, "expected at least one NPC speech action"
        for a in speeches:
            c = a.params.get("content")
            assert c and c.strip(), f"{a.actor_id} produced empty speech: {c!r}"


# --------------------------------------------------------------------------- #
# Opt-in real-Ollama integration (skipped by default)
# --------------------------------------------------------------------------- #

_OLLAMA = os.environ.get("RPG_OLLAMA_TEST") == "1"


@pytest.mark.skipif(not _OLLAMA, reason="set RPG_OLLAMA_TEST=1 to run real-Ollama dialogue test")
class TestDialogueRealOllama:
    """Exercises the real LLM end to end. Tolerates gpt-oss returning empties:
    the contract is that the *pipeline* never emits an empty NPC utterance."""

    def test_real_ollama_npc_speech_non_empty(self, tmp_path):
        from verisaria.runtime.session import GameSession
        session = GameSession(
            "fixtures/content_packs/valid_frontier_town.json",
            save_dir=str(tmp_path),
            llm_backend="ollama",
        )
        w = session.world.state
        pid = session.player_id
        ploc = w.entities[pid].location_id
        npc_ids = [eid for eid, e in w.entities.items() if e.entity_type == "npc"]
        for nid in npc_ids:
            w.entities[nid].location_id = ploc
            session.conversation_manager.start_session(
                initiator_id=pid, participants=[nid], tick=w.tick
            )
        for nid in npc_ids:
            sess = session.conversation_manager.get_active_session(nid)
            if sess is not None:
                sess.shared_context["last_speaker"] = pid
                sess.shared_context["last_content"] = "最近镇上怎么样？"

        speeches = [a for a in session._collect_npc_actions()
                    if a.action_type == ActionType.SPEECH]
        assert speeches
        for a in speeches:
            c = a.params.get("content")
            assert c and c.strip(), f"{a.actor_id} empty: {c!r}"


def test_dialogue_prompt_instructs_responding_to_player_utterance():
    """Issue A (hard playtest): NPCs ignored the player's question and emitted a
    generic in-character mutter. The prompt carried the player's line ("对方刚才说")
    but the OUTPUT instruction only asked for "a line in character", never to
    RESPOND to it. Assert both prompt paths now (a) surface the player's utterance
    and (b) instruct the NPC to answer/engage it."""
    gen = NPCDialogueGenerator(LLMOrchestrator(FakeLLMProvider()))
    session = SimpleNamespace(
        topic_stack=[],
        shared_context={"last_speaker": "player_001",
                        "last_content": "二十年前隘口到底发生了什么？"},
    )
    plain = gen._build_prompt_plain("npc.refugee_kaze", None, None, session)
    js = gen._build_prompt("npc.refugee_kaze", None, None, session)
    for prompt in (plain, js):
        assert "二十年前隘口到底发生了什么？" in prompt  # the player's line is present
        assert "回应" in prompt  # the NPC is told to respond to it


def test_dialogue_prompt_without_player_line_does_not_force_response():
    """When nobody just addressed the NPC (idle ambient line), the prompt should
    NOT demand a reply to a non-existent utterance."""
    gen = NPCDialogueGenerator(LLMOrchestrator(FakeLLMProvider()))
    session = SimpleNamespace(topic_stack=[], shared_context={})
    plain = gen._build_prompt_plain("npc.sentry_voss", None, None, session)
    assert "针对对方" not in plain  # no phantom "respond to them" directive


def test_dialogue_prompt_includes_npc_accessible_world_knowledge(tmp_path):
    """A5 layered truth (hard-playtest gap): the dialogue prompt referenced the
    world-book NOWHERE, so an NPC could never reveal what their scope says they
    know (kaze couldn't surface the massacre). Wire WorldBookFilter into the
    prompt: a refugee's prompt carries the forbidden massacre fact and CAN reveal
    it; the watch never even sees it, so they cannot reveal what they shouldn't."""
    from verisaria.runtime.session import GameSession

    s = GameSession(
        "fixtures/content_packs/frostgate_watchpost.json",
        save_dir=str(tmp_path), llm_backend="ollama",
    )
    gen = NPCDialogueGenerator(
        LLMOrchestrator(FakeLLMProvider()), world_book=s.pack.world_book
    )
    kaze = s.world.state.get_entity("npc.refugee_kaze")   # faction: refugees
    voss = s.world.state.get_entity("npc.sentry_voss")    # faction: watch

    pk = gen._build_prompt("npc.refugee_kaze", kaze, None, None)
    pv = gen._build_prompt("npc.sentry_voss", voss, None, None)
    assert "屠杀" in pk       # refugees may know the massacre
    assert "屠杀" not in pv   # the watch must not even see it (A5)

    pk_plain = gen._build_prompt_plain("npc.refugee_kaze", kaze, None, None)
    assert "屠杀" in pk_plain  # streaming path mirrors it


def test_dialogue_generator_without_world_book_is_unaffected():
    """Back-compat: omitting world_book yields no knowledge section (and no crash)."""
    gen = NPCDialogueGenerator(LLMOrchestrator(FakeLLMProvider()))
    p = gen._build_prompt("npc.refugee_kaze", None, None, None)
    assert "屠杀" not in p


def test_recent_spoken_lines_injected_with_anti_repeat_instruction():
    """Hard-playtest bug B2: NPCs repeated lines verbatim across ticks (voss said
    '我怕…撑不住' on two turns). Feed an NPC's recently-spoken lines back into its
    prompt with an avoid-repetition directive so it varies."""
    gen = NPCDialogueGenerator(LLMOrchestrator(FakeLLMProvider()))
    # Nothing said yet -> no recent section.
    assert "最近" not in gen._build_prompt("npc.sentry_voss", None, None, None)

    gen.note_spoken("npc.sentry_voss", "我怕还没等到补给来，哨站就先撑不住了。")
    p = gen._build_prompt("npc.sentry_voss", None, None, None)
    assert "我怕还没等到补给来，哨站就先撑不住了。" in p
    assert "重复" in p  # an avoid-repetition instruction is present
    # Another NPC is unaffected by voss's history.
    assert "撑不住" not in gen._build_prompt("npc.captain_brann", None, None, None)


def test_recent_lines_are_capped_and_per_npc():
    """The recent-lines buffer is bounded (keeps only the latest few)."""
    gen = NPCDialogueGenerator(LLMOrchestrator(FakeLLMProvider()))
    for i in range(8):
        gen.note_spoken("npc.sentry_voss", f"第{i}句")
    p = gen._build_prompt("npc.sentry_voss", None, None, None)
    assert "第7句" in p and "第6句" in p  # most recent kept
    assert "第0句" not in p               # oldest evicted


def test_knowledge_section_directs_engaging_the_question(tmp_path):
    """Playtest: kaze sometimes drifted off-topic ('还能撑多久') when asked about the
    massacre he knows. When an NPC holds accessible lore, the prompt must tell them
    to RESPOND to a question about it directly (guard/reveal/deflect — but engage),
    not wander to an unrelated topic. A5 'can't fabricate' must remain intact."""
    from verisaria.runtime.session import GameSession

    s = GameSession(
        "fixtures/content_packs/frostgate_watchpost.json",
        save_dir=str(tmp_path), llm_backend="ollama",
    )
    gen = NPCDialogueGenerator(
        LLMOrchestrator(FakeLLMProvider()), world_book=s.pack.world_book
    )
    kaze = s.world.state.get_entity("npc.refugee_kaze")
    p = gen._build_prompt("npc.refugee_kaze", kaze, None, None)
    assert "正面回应" in p              # engage a question about what you know
    assert "无关" in p                  # don't drift to an unrelated topic
    assert "不可能知道的事" in p        # A5 anti-fabrication still present


def test_dialogue_prompt_includes_perceivable_environment(tmp_path):
    """NPCs had zero spatial awareness in-prompt (env only leaked via memories,
    which let high-salience survival memories dominate and pull replies off-topic).
    Wire in the NPC's PERCEIVABLE surroundings: where they are + who is co-located.
    A5: only co-located entities are visible — never NPCs in other locations."""
    from verisaria.runtime.session import GameSession

    s = GameSession(
        "fixtures/content_packs/frostgate_watchpost.json",
        save_dir=str(tmp_path), llm_backend="ollama",
    )
    brann = s.world.state.get_entity("npc.captain_brann")  # gatehouse, with voss
    gen = NPCDialogueGenerator(LLMOrchestrator(FakeLLMProvider()))

    p = gen._build_prompt("npc.captain_brann", brann, None, None, world=s.world.state)
    assert "处境" in p                       # an environment section is present
    assert "sentry_voss" in p                # co-located → perceivable
    assert "quartermaster_hale" not in p     # at barracks → NOT perceivable (A5)
    assert "refugee_kaze" not in p           # at refugee_camp → NOT perceivable (A5)


def test_dialogue_prompt_environment_is_optional(tmp_path):
    """Without a world handle, no environment section (and no crash)."""
    gen = NPCDialogueGenerator(LLMOrchestrator(FakeLLMProvider()))
    assert "处境" not in gen._build_prompt("npc.captain_brann", None, None, None)


def _mk_mem(owner, content, salience):
    from verisaria.engine.schemas import Memory, MemoryLayer
    return Memory(
        memory_id=f"m{abs(hash(content))%99999}", owner_id=owner, content=content,
        layer=MemoryLayer.WORKING, salience=salience, decay_rate=0.05, tick=0,
        source_event_ids=[],
    )


def test_memory_block_ranks_by_relevance_to_query():
    """Root cause of kaze's drift: the memory block ranked by salience ALONE, so
    high-salience survival memories crowded out the lower-salience massacre memory
    even when the player asked about the massacre. Ranking by salience × relevance
    lifts the on-topic memory into the (small) top-N."""
    from verisaria.engine.memory import MemoryStore

    gen = NPCDialogueGenerator(LLMOrchestrator(FakeLLMProvider()))
    store = MemoryStore()
    for c, sal in [
        ("听到：守军要削减口粮", 0.9),
        ("听到：营里怨声载道", 0.85),
        ("听到：又有人被拒之门外", 0.8),
        ("你知道：二十年前隘口屠杀了前来求和的使节", 0.4),
    ]:
        store.add("npc.x", _mk_mem("npc.x", c, sal))

    # Salience-only (no query) drops the low-salience massacre memory from top-3.
    assert "屠杀" not in gen._memory_block("npc.x", store)
    # Relevance to the question lifts it back in.
    relevant = gen._memory_block("npc.x", store, query="二十年前隘口屠杀的事")
    assert "屠杀" in relevant


def test_memory_block_without_query_is_salience_order():
    """Back-compat: no query → unchanged salience-ordered behaviour."""
    from verisaria.engine.memory import MemoryStore

    gen = NPCDialogueGenerator(LLMOrchestrator(FakeLLMProvider()))
    store = MemoryStore()
    store.add("npc.x", _mk_mem("npc.x", "高显著记忆", 0.9))
    store.add("npc.x", _mk_mem("npc.x", "低显著记忆", 0.1))
    block = gen._memory_block("npc.x", store)
    assert block.index("高显著记忆") < block.index("低显著记忆")
