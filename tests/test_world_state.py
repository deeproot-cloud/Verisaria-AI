"""PLAY-3 Channel C (slice 1a) — player choices change world FACTS.

The pack declares mutable world-state variables (e.g. ``refugees_admitted``).
A validated state change to ``world.<var_id>`` flips the fact (gated: only
declared, mutable vars), the world fact reaches the campaign-driver context (so
the plot reacts) and persists across save/load.

slice 1a is fully deterministic — no LLM. slice 1b will teach the arbiter to
*propose* these changes from freeform player actions.
"""
from __future__ import annotations

from types import SimpleNamespace

from verisaria.engine.campaign import CampaignDriverManager
from verisaria.runtime.session import GameSession
from verisaria.engine.schemas import Action, ActionType, CommitmentLevel, ParsedIntent, StateChange

PACK = "fixtures/content_packs/frostgate_watchpost.json"
VAR = "refugees_admitted"


def _decree_action() -> Action:
    """A freeform player action that should route through the arbiter."""
    return Action(
        action_id="act_0_1",
        actor_id="player_001",
        action_type=ActionType.PHYSICAL,
        target_id=None,
        params={"verb": "下令放难民进来"},
        tick=0,
    )


def test_pack_declared_world_var_initializes(tmp_path):
    s = GameSession(PACK, save_dir=str(tmp_path))
    assert s.get_world_var(VAR) is False


def test_set_world_var_is_gated_by_declaration(tmp_path):
    s = GameSession(PACK, save_dir=str(tmp_path))
    # Declared + mutable → applied.
    assert s.set_world_var(VAR, True) is True
    assert s.get_world_var(VAR) is True
    # Undeclared → rejected, never created.
    assert s.set_world_var("totally_made_up_flag", True) is False
    assert s.get_world_var("totally_made_up_flag") is None


def test_accepted_state_change_routes_world_field(tmp_path):
    """An arbiter-accepted change to a `world.*` field flips the world var
    (the real path arbiter outcomes flow through)."""
    s = GameSession(PACK, save_dir=str(tmp_path))
    outcome = SimpleNamespace(
        accepted_state_changes=[
            StateChange(field=f"world.{VAR}", delta=True, reason="守备官下令放行")
        ]
    )
    s._apply_state_changes(outcome)
    assert s.get_world_var(VAR) is True


def test_world_var_reaches_campaign_context_and_drives_driver(tmp_path):
    s = GameSession(PACK, save_dir=str(tmp_path))
    assert s._build_campaign_context()["world"].get(VAR) is False

    s.set_world_var(VAR, True)
    assert s._build_campaign_context()["world"].get(VAR) is True

    mgr = CampaignDriverManager.from_dicts([{
        "driver_id": "camp_strains",
        "type": "consequence",
        "signals": [{"condition": f"world.{VAR}", "weight": 1.0}],
        "possible_events": [{"event_type": "camp_overcrowding_tension", "probability": 1.0}],
        "severity": 0.5,
        "cooldown_ticks": 6,
    }])
    assert mgr.check_all({"world": {VAR: False}}, tick=1) == []
    assert mgr.check_all({"world": {VAR: True}}, tick=2) != []


def test_world_var_survives_save_load(tmp_path):
    s = GameSession(PACK, save_dir=str(tmp_path))
    s.set_world_var(VAR, True)
    saved = s._handle_command("/save")
    save_id = saved.split(":", 1)[1].strip()

    reloaded = GameSession(PACK, save_dir=str(tmp_path))
    assert reloaded.get_world_var(VAR) is False  # fresh
    reloaded._handle_command(f"/load {save_id}")
    assert reloaded.get_world_var(VAR) is True


def test_world_command_shows_state(tmp_path):
    s = GameSession(PACK, save_dir=str(tmp_path))
    out = s._handle_command("/world")
    assert out is not None
    assert VAR in out or "难民" in out


# --- slice 1b: arbiter proposes world-var changes from freeform actions ---

def test_arbiter_prompt_advertises_mutable_world_vars(tmp_path):
    """The arbiter must be told which world facts are mutable, or the LLM can
    never know it may propose changing them."""
    s = GameSession(PACK, save_dir=str(tmp_path))
    seen: list[str] = []
    orig = s.arbiter.llm.call

    def spy(request):
        if request.task_type == "arbiter_decide":
            seen.append(request.prompt)
        return orig(request)

    s.arbiter.llm.call = spy
    s._handle_arbiter_action(_decree_action())

    assert seen, "an arbiter call should have been made"
    assert any(VAR in p or "难民是否获准入营" in p for p in seen)


def _speech_to(target: str, content: str):
    from verisaria.engine.schemas import CommitmentLevel
    return ParsedIntent(
        intent_id="i", source="natural_language", raw_text=content,
        intent_type=ActionType.SPEECH, actor_id="player_001", target_id=target,
        content=content, commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content=content, timestamp=0,
    )


def _register_arbiter(session, comply: bool):
    changes = (
        [{"field": f"world.{VAR}", "delta": True, "reason": "队长批准"}] if comply else []
    )
    session.llm_provider.register_fixture(
        task_type="arbiter_decide", prompt="__arb__",
        expected_output={
            "arbiter_id": "a", "source_action_id": "x",
            "outcome": "success" if comply else "failure",
            "reason": "队长同意开门" if comply else "队长拒绝",
            "evidence_refs": [], "state_changes_proposed": changes,
            "confidence": 0.9,
            "narration_hint": "城门缓缓打开" if comply else "城门依旧紧闭",
        },
    )


def test_world_request_to_authority_flips_on_comply(tmp_path):
    """A world-changing request to the AUTHORIZED NPC (brann) is adjudicated; on
    compliance the world fact flips — persuading the right person works."""
    s = GameSession(PACK, save_dir=str(tmp_path))
    s.intent_parser.parse = lambda raw_text, **kw: _speech_to("npc.captain_brann", raw_text)
    _register_arbiter(s, comply=True)
    assert s.get_world_var(VAR) is False
    s.run_tick("队长，以你的职权下令开城门，放难民进来吧。")
    assert s.get_world_var(VAR) is True


def test_world_request_refused_keeps_false(tmp_path):
    s = GameSession(PACK, save_dir=str(tmp_path))
    s.intent_parser.parse = lambda raw_text, **kw: _speech_to("npc.captain_brann", raw_text)
    _register_arbiter(s, comply=False)
    s.run_tick("队长，开门放难民进来。")
    assert s.get_world_var(VAR) is False


def test_partial_success_does_not_flip_world(tmp_path):
    """Playtest B4: the arbiter LLM labelled the verdict 'partial_success' ("我再想想")
    yet still proposed opening the gate. The world flipped True while the captain
    only deferred — a state/fiction mismatch, and it made the C-loop trivially easy.
    A world fact must flip ONLY on a genuine 'success' verdict."""
    s = GameSession(PACK, save_dir=str(tmp_path))
    s.intent_parser.parse = lambda raw_text, **kw: _speech_to("npc.captain_brann", raw_text)
    s.llm_provider.register_fixture(
        task_type="arbiter_decide", prompt="__arb__",
        expected_output={
            "arbiter_id": "a", "source_action_id": "x",
            "outcome": "partial_success",
            "reason": "队长还在犹豫，没有当场答应",
            "evidence_refs": [],
            "state_changes_proposed": [
                {"field": f"world.{VAR}", "delta": True, "reason": "x"}
            ],
            "confidence": 0.7, "narration_hint": "",
        },
    )
    s.run_tick("队长，开门放难民进来吧。")
    assert s.get_world_var(VAR) is False  # a deferral must NOT open the gate


def test_request_to_non_authority_npc_does_not_change_world(tmp_path):
    """Persuading a powerless NPC (voss) can't change the world — even if the
    arbiter would have complied, the request never routes there."""
    s = GameSession(PACK, save_dir=str(tmp_path))
    s.intent_parser.parse = lambda raw_text, **kw: _speech_to("npc.sentry_voss", raw_text)
    _register_arbiter(s, comply=True)
    s.run_tick("伏斯，开门放难民进来。")
    assert s.get_world_var(VAR) is False


def test_authority_response_is_voiced_not_arbiter_reason(tmp_path):
    """The authority's reply to the player must be an in-character line, not the
    arbiter's analytical reason (which leaks numbers / meta tone)."""
    s = GameSession(PACK, save_dir=str(tmp_path))
    s.intent_parser.parse = lambda raw_text, **kw: _speech_to("npc.captain_brann", raw_text)
    s.llm_provider.register_fixture(
        task_type="arbiter_decide", prompt="__a__",
        expected_output={
            "arbiter_id": "a", "source_action_id": "x", "outcome": "success",
            "reason": "ARBITER_ANALYSIS_信任度高0.85", "evidence_refs": [],
            "state_changes_proposed": [{"field": f"world.{VAR}", "delta": True, "reason": "ok"}],
            "confidence": 0.9, "narration_hint": "",
        },
    )
    s.npc_dialogue_generator.generate_line = (
        lambda **k: "好，开门，让他们进来吧——后果我担。"
        if k.get("npc_id") == "npc.captain_brann" else None
    )
    out = s.run_tick("队长，开门放难民进来。")
    assert "好，开门，让他们进来吧" in out          # voiced, in-character
    assert "ARBITER_ANALYSIS" not in out           # arbiter's reason NOT leaked
    assert s.get_world_var(VAR) is True             # comply → flipped


def test_arbiter_sees_authority_relationship(tmp_path):
    """The arbiter must weigh the authorized NPC's stance toward the player, so
    the player's relationship work (Channel A) gates the world change."""
    s = GameSession(PACK, save_dir=str(tmp_path))
    s.relationship_store.apply("npc.captain_brann", "player_001",
                               {"trust": 0.6, "suspicion": 0.2}, tick=0)
    s.intent_parser.parse = lambda raw_text, **kw: _speech_to("npc.captain_brann", raw_text)
    seen: list[str] = []
    orig = s.arbiter.llm.call

    def spy(req):
        if req.task_type == "arbiter_decide":
            seen.append(req.prompt)
        return orig(req)

    s.arbiter.llm.call = spy
    s.run_tick("队长，开门放难民进来。")
    assert seen, "the request should route to the arbiter"
    joined = "\n".join(seen)
    assert "brann" in joined or "布兰" in joined
    assert "信任" in joined or "trust" in joined.lower()


def test_arbiter_proposed_world_change_is_applied(tmp_path):
    """When the arbiter accepts a `world.*` change, the world fact flips —
    closing the loop from a freeform player action to a changed world."""
    s = GameSession(PACK, save_dir=str(tmp_path))
    s.llm_provider.register_fixture(
        task_type="arbiter_decide",
        prompt="__arbiter__",
        expected_output={
            "arbiter_id": "arb_x",
            "source_action_id": "act_x",
            "outcome": "success",
            "reason": "守备官下令放行，难民获准入营。",
            "evidence_refs": [],
            "state_changes_proposed": [
                {"field": f"world.{VAR}", "delta": True, "reason": "下令放行"}
            ],
            "confidence": 0.9,
            "narration_hint": "厚重的城门缓缓打开。",
        },
    )

    assert s.get_world_var(VAR) is False
    s._handle_arbiter_action(_decree_action())
    assert s.get_world_var(VAR) is True
