"""PLAY-3 Channel A — player choices have consequences via LLM appraisal.

When an NPC perceives a socially-weighted player action, an appraisal step
(through the LLM provider seam, so FakeLLM/replay stays deterministic) yields
bounded relationship-dimension deltas toward the player. Those deltas accumulate
into a stored relationship snapshot the player can query.

These tests assert STRUCTURE and DIRECTION (suspicion went up, trust did not),
never the exact LLM-produced magnitude.
"""
from __future__ import annotations

from verisaria.engine.appraisal import RelationshipStore
from verisaria.runtime.session import GameSession
from verisaria.engine.schemas import ActionType, CommitmentLevel, ParsedIntent

PACK = "fixtures/content_packs/frostgate_watchpost.json"
VOSS = "npc.sentry_voss"
PLAYER = "player_001"


def _speech_to_voss(raw_text: str) -> ParsedIntent:
    """Deterministic parse: player addresses voss, isolating the appraisal
    feature from intent parsing."""
    return ParsedIntent(
        intent_id="intent_appraise",
        source="natural_language",
        raw_text=raw_text,
        intent_type=ActionType.SPEECH,
        actor_id=PLAYER,
        target_id=VOSS,
        content=raw_text,
        commitment=CommitmentLevel.COMMITTED,
        confidence=0.9,
        performed_content=raw_text,
        timestamp=0,
    )


def _register_appraisal(session: GameSession, deltas: dict[str, float]) -> None:
    """Inject a single canned appraisal fixture for the appraisal task_type.
    FakeLLM's single-fixture-per-task_type fallback makes it match any prompt."""
    session.llm_provider.register_fixture(
        task_type="appraise_relationship",
        prompt="__appraisal__",
        expected_output={
            "belief": f"{PLAYER} 在替难民说话，这让我不安。",
            "deltas": deltas,
            "reason": "玩家公开维护恶魔难民，触动了我对教会教义的信奉。",
        },
    )


def test_player_action_moves_npc_relationship_in_appraised_direction(tmp_path):
    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech_to_voss(raw_text)
    _register_appraisal(session, deltas={"suspicion": 0.2, "trust": -0.1})

    # Baseline: no stored relationship toward the player yet.
    assert session.get_relationship(VOSS, PLAYER) is None

    session.run_tick("难民也是人，应该放他们进来。")

    snap = session.get_relationship(VOSS, PLAYER)
    assert snap is not None, "appraisal should have created a relationship snapshot"
    # Direction, not magnitude: suspicion rose above the 0 baseline.
    assert snap.dimensions.get("suspicion", 0.0) > 0.0
    # trust delta was negative against a 0 floor → stays clamped at 0, not raised.
    assert snap.dimensions.get("trust", 0.0) == 0.0


def test_appraisal_deltas_accumulate_across_ticks(tmp_path):
    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech_to_voss(raw_text)
    _register_appraisal(session, deltas={"suspicion": 0.2})

    session.run_tick("难民也是人。")
    first = session.get_relationship(VOSS, PLAYER).dimensions["suspicion"]
    session.run_tick("教会的说法未必可信。")
    second = session.get_relationship(VOSS, PLAYER).dimensions["suspicion"]

    assert second > first, "repeated appraised actions should accumulate"


def test_appraisal_delta_is_clamped_to_unit_range(tmp_path):
    """A single absurd LLM delta cannot blow a dimension past 1.0."""
    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech_to_voss(raw_text)
    _register_appraisal(session, deltas={"suspicion": 5.0})

    session.run_tick("难民也是人。")
    snap = session.get_relationship(VOSS, PLAYER)
    assert 0.0 <= snap.dimensions["suspicion"] <= 1.0


def test_relationship_apply_has_diminishing_returns():
    """Repeated same-direction appraisals should move a dimension fast while it
    is neutral and ever-slower as it strengthens — approaching, never slamming
    into, the bound. (Linear accumulation saturated at 1.0 in ~6 turns.)"""
    store = RelationshipStore()
    vals = [0.0]
    for t in range(8):
        store.apply("npc.x", PLAYER, {"trust": 0.2}, tick=t)
        vals.append(store.get("npc.x", PLAYER).dimensions["trust"])

    increments = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
    # Each step moves less than the previous one (strictly diminishing).
    assert all(increments[i + 1] < increments[i] for i in range(len(increments) - 1))
    # Asymptotic: even 8 strong positive appraisals never hit the ceiling.
    assert vals[-1] < 1.0


def test_relationship_negative_deltas_also_diminish_toward_floor():
    store = RelationshipStore()
    store.apply("npc.x", PLAYER, {"trust": 0.3}, tick=0)  # seed some trust
    seeded = store.get("npc.x", PLAYER).dimensions["trust"]
    store.apply("npc.x", PLAYER, {"trust": -0.2}, tick=1)
    after = store.get("npc.x", PLAYER).dimensions["trust"]
    assert 0.0 <= after < seeded  # decreased, stayed in range


def test_appraisal_concurrent_path_applies_all_perceivers(tmp_path):
    """When the provider supports concurrency, every perceiving NPC's appraisal
    is still applied (results collected in order, mutations serial) — concurrency
    must not drop or corrupt any perceiver's relationship update."""
    from verisaria.engine.appraisal import AppraisalResult

    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech_to_voss(raw_text)
    # Force the concurrent code path; results stay deterministic (ordered map +
    # serial apply), so the assertion is stable.
    session.llm_provider.supports_concurrency = True

    def fake_appraise(npc_id, entity, event, world=None, memory_store=None,
                      known_facts=None, leverage=None):
        return AppraisalResult(belief=f"{npc_id} sees the player", deltas={"suspicion": 0.2}, reason="r")

    session.relationship_appraiser.appraise = fake_appraise
    session.run_tick("难民也是人，应该放他们进来。")

    # Both gatehouse NPCs perceived the player's speech and were appraised.
    assert session.get_relationship(VOSS, PLAYER) is not None
    assert session.get_relationship("npc.captain_brann", PLAYER) is not None


def test_appraisal_backgrounded_for_real_providers_and_flushed_on_read(tmp_path):
    """For real providers the appraisal runs off the player's critical path
    (PLAY-1): run_tick leaves it pending; any relationship read flushes + applies
    it, so the player never sees stale data."""
    from verisaria.engine.appraisal import AppraisalResult

    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech_to_voss(raw_text)
    session.llm_provider.supports_concurrency = True  # real-provider path
    session.relationship_appraiser.appraise = (
        lambda *a, **k: AppraisalResult(belief="b", deltas={"suspicion": 0.2}, reason="r")
    )

    session.run_tick("难民也是人，应该放他们进来。")
    # Deferred off the critical path — not yet applied.
    assert session._pending_appraisal is not None

    snap = session.get_relationship(VOSS, PLAYER)  # reading flushes it
    assert snap is not None and snap.dimensions["suspicion"] > 0
    assert session._pending_appraisal is None


def test_fakellm_appraisal_stays_inline(tmp_path):
    """A10: under FakeLLM the appraisal runs inline/serial (never backgrounded),
    so replays stay deterministic."""
    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech_to_voss(raw_text)
    _register_appraisal(session, deltas={"suspicion": 0.2})

    session.run_tick("难民也是人，应该放他们进来。")
    assert session._pending_appraisal is None  # applied inline, nothing pending
    assert session.get_relationship(VOSS, PLAYER).dimensions["suspicion"] > 0


def test_save_flushes_pending_appraisal(tmp_path):
    """Saving must barrier the background appraisal so the save isn't missing the
    just-earned relationship change."""
    from verisaria.engine.appraisal import AppraisalResult

    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech_to_voss(raw_text)
    session.llm_provider.supports_concurrency = True
    session.relationship_appraiser.appraise = (
        lambda *a, **k: AppraisalResult(belief="b", deltas={"suspicion": 0.2}, reason="r")
    )
    session.run_tick("难民也是人，应该放他们进来。")
    # Save WITHOUT reading first — must still flush.
    saved = session._handle_command("/save")
    save_id = saved.split(":", 1)[1].strip()

    reloaded = GameSession(PACK, save_dir=str(tmp_path))
    reloaded._handle_command(f"/load {save_id}")
    assert reloaded.get_relationship(VOSS, PLAYER) is not None


def test_appraisal_belief_is_remembered(tmp_path):
    """The appraisal's belief is written into the NPC's memory, so the stance
    change is remembered and can surface in later dialogue."""
    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech_to_voss(raw_text)
    session.llm_provider.register_fixture(
        task_type="appraise_relationship",
        prompt="__appraisal__",
        expected_output={
            "belief": "这个新来的人竟敢替恶魔难民说话，我对他起了戒心。",
            "deltas": {"suspicion": 0.2},
            "reason": "他的话与教会的教诲相悖。",
        },
    )

    session.run_tick("难民也是人，应该放他们进来。")

    memories = [m.content for m in session.memory_store.get_all(VOSS)]
    assert any("起了戒心" in c for c in memories), (
        f"appraisal belief should be remembered; voss memories = {memories}"
    )


def test_appraisal_prompt_is_grounded_in_npc_world_book(tmp_path):
    """The appraisal prompt must include what the NPC actually knows/believes
    (its accessible world-book), so the stance is grounded in the NPC's own
    worldview (A5) — not just its trait labels."""
    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech_to_voss(raw_text)
    _register_appraisal(session, deltas={"suspicion": 0.2})

    seen_prompts: list[str] = []
    orig_call = session.relationship_appraiser.llm.call

    def spy(request):
        if request.task_type == "appraise_relationship":
            seen_prompts.append(request.prompt)
        return orig_call(request)

    session.relationship_appraiser.llm.call = spy

    session.run_tick("难民也是人，应该放他们进来。")

    assert seen_prompts, "an appraisal call should have been made for voss"
    # voss is faction 'watch' → has access to the church propaganda world-book
    # entry; grounding means that knowledge appears in the appraisal prompt.
    assert any("圣焰教会" in p for p in seen_prompts), (
        "appraisal prompt should be grounded in the NPC's accessible world-book"
    )


def test_relationship_survives_save_load(tmp_path):
    """An appraised relationship is durable across a save/load round-trip."""
    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech_to_voss(raw_text)
    _register_appraisal(session, deltas={"suspicion": 0.2})
    session.run_tick("难民也是人，应该放他们进来。")
    before = session.get_relationship(VOSS, PLAYER).dimensions["suspicion"]
    assert before > 0.0

    saved = session._handle_command("/save")
    save_id = saved.split(":", 1)[1].strip()

    reloaded = GameSession(PACK, save_dir=str(tmp_path))
    assert reloaded.get_relationship(VOSS, PLAYER) is None  # fresh session
    reloaded._handle_command(f"/load {save_id}")

    after = reloaded.get_relationship(VOSS, PLAYER)
    assert after is not None, "relationship should be restored after load"
    assert after.dimensions["suspicion"] == before


def test_relationship_command_shows_appraised_npc(tmp_path):
    """`/relationship` surfaces the player-facing consequence of their choices."""
    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech_to_voss(raw_text)
    _register_appraisal(session, deltas={"suspicion": 0.2})

    # Before any appraised interaction, the view is empty (not an error).
    empty = session._handle_command("/relationship")
    assert empty is not None

    session.run_tick("难民也是人，应该放他们进来。")
    out = session._handle_command("/relationship")
    assert out is not None
    assert "voss" in out
    assert "suspicion" in out or "怀疑" in out


def test_appraisal_prompt_invites_a_downward_path():
    """Playability audit #5: the appraisal prompt must explicitly allow suspicion to
    FALL / trust to rise for sincere/fair actions — not only ever rise on pressure."""
    from types import SimpleNamespace
    from verisaria.engine.appraisal import RelationshipAppraiser
    from verisaria.engine.llm import FakeLLMProvider, LLMOrchestrator

    appr = RelationshipAppraiser(LLMOrchestrator(primary_provider=FakeLLMProvider()))
    prompt = appr._build_prompt(
        "npc.x", None, SimpleNamespace(summary="玩家诚恳道歉并讲清了道理"))
    assert "下降" in prompt           # suspicion can fall (general downward path)
    assert "鼓励" in prompt           # framed as normal/encouraged, not an exception
    # audit 5 #2 — leverage model: a wary/pressed principal softens only when the
    # words are BACKED; pure sweet-talk is discounted, not punished (fixes the round-6
    # backfire where giving an out *raised* suspicion).
    assert "实据" in prompt or "筹码" in prompt
    assert "既不该让你更信任、也不该让你更怀疑" in prompt


def test_appraisal_prompt_renders_leverage_block():
    from types import SimpleNamespace
    from verisaria.engine.appraisal import RelationshipAppraiser
    from verisaria.engine.llm import FakeLLMProvider, LLMOrchestrator
    appr = RelationshipAppraiser(LLMOrchestrator(primary_provider=FakeLLMProvider()))
    ev = SimpleNamespace(summary="玩家保证不写她的名字")

    none = appr._build_prompt("npc.sula", None, ev)
    assert "并无实据撑腰" in none            # no leverage → "just words"
    backed = appr._build_prompt("npc.sula", None, ev, leverage=["你已查到她私抽热能的账"])
    assert "你已查到她私抽热能的账" in backed  # leverage surfaced to the appraiser
