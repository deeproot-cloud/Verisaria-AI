"""PLAY-3 Channel B — repeated player intent aggregates into a confirmed stance.

The agenda machinery (signal → cluster → proposal → confirmed Drive) already
existed, but the CLI never ran aggregation: ``aggregate_signals`` was only
reachable inside a reflection scene, which itself only triggered on
``unconfirmed_inferences`` — i.e. on proposals that only aggregation produces.
That deadlock left ``/agenda`` permanently showing "[已确认目标] 无".

These tests assert that repeating an intent makes it a confirmed goal the player
can see (and that it survives save/load — the agenda already persists).
"""
from __future__ import annotations

from verisaria.engine.agenda import AgendaService
from verisaria.engine.campaign import CampaignDriverManager
from verisaria.runtime.session import GameSession
from verisaria.engine.schemas import ActionType, CommitmentLevel, ParsedIntent

PACK = "fixtures/content_packs/frostgate_watchpost.json"
VOSS = "npc.sentry_voss"
PLAYER = "player_001"


def _speech(raw_text: str) -> ParsedIntent:
    return ParsedIntent(
        intent_id="intent_stance",
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


def test_repeated_intent_becomes_confirmed_goal(tmp_path):
    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech(raw_text)

    start = session.agenda_service.get_agenda(session.world.state.tick)
    assert start.current_drives == []

    for _ in range(6):
        session.run_tick("我想帮助难民，让他们进来。")

    drives = session.agenda_service.get_agenda(session.world.state.tick).current_drives
    assert drives, "repeated intent should aggregate into a confirmed goal"
    assert any("难民" in d.label for d in drives)


def test_single_intent_does_not_confirm_a_goal(tmp_path):
    """One-off intents must not spuriously become confirmed goals — confirmation
    requires sustained repetition (MIN_SIGNAL_COUNT)."""
    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech(raw_text)

    session.run_tick("我想帮助难民。")
    drives = session.agenda_service.get_agenda(session.world.state.tick).current_drives
    assert drives == []


def test_confirmed_goal_shows_in_agenda_command(tmp_path):
    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech(raw_text)
    for _ in range(6):
        session.run_tick("我想帮助难民，让他们进来。")

    out = session._handle_command("/agenda")
    # The confirmed-goals section is no longer empty.
    confirmed_section = out.split("[已确认目标]", 1)[1]
    assert "难民" in confirmed_section


def test_confirmed_goal_survives_save_load(tmp_path):
    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech(raw_text)
    for _ in range(6):
        session.run_tick("我想帮助难民，让他们进来。")
    before = [d.label for d in
              session.agenda_service.get_agenda(session.world.state.tick).current_drives]
    assert before

    saved = session._handle_command("/save")
    save_id = saved.split(":", 1)[1].strip()

    reloaded = GameSession(PACK, save_dir=str(tmp_path))
    reloaded._handle_command(f"/load {save_id}")
    after = [d.label for d in
             reloaded.agenda_service.get_agenda(reloaded.world.state.tick).current_drives]
    assert after == before


# --- B2-A1: pack-declared stance topics → robust clustering + world reads it ---

STANCE_TOPICS = {"help_refugees": ["难民", "放他们", "收留", "进来", "庇护"]}


def test_pack_topics_cluster_synonyms_into_one_stance():
    """Differently-phrased intents that share a pack topic cluster together —
    fixing the bigram limitation where synonyms scattered."""
    svc = AgendaService(player_id=PLAYER, stance_topics=STANCE_TOPICS)
    notes = ["难民也是人", "放他们进来", "该收留这些人", "给他们庇护", "别把难民关在外面"]
    for i, n in enumerate(notes):
        svc.add_signal(note=n, tick=i + 1, source_id=f"s{i}")

    confirmed = svc.aggregate_and_autoconfirm(current_tick=6)
    assert confirmed, "five same-topic intents should confirm a stance"
    assert "help_refugees" in svc.get_confirmed_stance_topics()


def test_repeated_intent_confirms_one_goal_per_topic_with_pack_label():
    """Repeating a stance over many ticks must yield ONE confirmed goal for the
    topic (labeled by the pack), not a new verbatim-quote goal every tick, and it
    must not also show as a pending inference."""
    svc = AgendaService(
        player_id=PLAYER,
        stance_topics={"help_refugees": ["难民", "进来", "收留", "庇护"]},
        stance_labels={"help_refugees": "接纳难民"},
    )
    for i in range(12):
        svc.add_signal(note=f"放难民进来，给他们庇护（第{i}次）", tick=i + 1, source_id=f"s{i}")
        svc.aggregate_and_autoconfirm(current_tick=i + 1)

    ag = svc.get_agenda(current_tick=12)
    labels = [d.label for d in ag.current_drives]
    assert labels == ["接纳难民"]                       # one goal, pack label
    assert not any(si.get("claim") == "接纳难民" for si in ag.system_inferred)  # no dup


def test_confirmed_stance_topics_survive_save_load():
    svc = AgendaService(player_id=PLAYER, stance_topics=STANCE_TOPICS)
    for i, n in enumerate(["难民也是人", "放他们进来", "该收留", "给他们庇护", "难民别关外面"]):
        svc.add_signal(note=n, tick=i + 1, source_id=f"s{i}")
    svc.aggregate_and_autoconfirm(current_tick=6)
    assert "help_refugees" in svc.get_confirmed_stance_topics()

    restored = AgendaService(player_id=PLAYER, stance_topics=STANCE_TOPICS)
    restored.load_state(svc.get_state())
    assert "help_refugees" in restored.get_confirmed_stance_topics()


def test_confirmed_stance_reaches_campaign_context(tmp_path):
    """After a stance confirms, it appears in the campaign-driver context so the
    world/plot can react (A5-safe: this is world-level, not NPC cognition)."""
    session = GameSession(PACK, save_dir=str(tmp_path))
    session.intent_parser.parse = lambda raw_text, **kw: _speech(raw_text)

    assert "help_refugees" not in session._build_campaign_context().get("stance", {})

    for _ in range(6):
        session.run_tick("难民也是人，放他们进来。")

    stance = session._build_campaign_context().get("stance", {})
    assert stance.get("help_refugees") is True


def test_stance_gated_driver_fires_only_when_stance_present():
    """A pack driver conditioned on a stance fires once the player holds it."""
    mgr = CampaignDriverManager.from_dicts([{
        "driver_id": "watch_wary",
        "type": "faction_response",
        "signals": [{"condition": "stance.help_refugees", "weight": 1.0}],
        "possible_events": [{"event_type": "watch_grows_wary_of_player", "probability": 1.0}],
        "severity": 0.5,
        "cooldown_ticks": 8,
    }])

    assert mgr.check_all({"stance": {}}, tick=1) == []
    seeds = mgr.check_all({"stance": {"help_refugees": True}}, tick=2)
    assert any(s["event_type"] == "watch_grows_wary_of_player" for s in seeds)
