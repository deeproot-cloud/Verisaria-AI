"""World clock — variable in-world time keyed off the pacing verdict (not tick×const),
plus time-of-day derivation. Pure functions; see docs/design/worldclock-and-weather.md."""
from __future__ import annotations

import pytest

from verisaria.engine import worldclock as wc
from verisaria.engine.schemas import PacingSpeed


def test_minutes_per_step_scales_with_pacing_density():
    # a charged beat (conversation/combat) is short; a quiet fast-forward step is long
    assert wc.minutes_for_step(PacingSpeed.PAUSE) < wc.minutes_for_step(PacingSpeed.SLOW)
    assert wc.minutes_for_step(PacingSpeed.SLOW) < wc.minutes_for_step(PacingSpeed.FORCE)
    assert wc.minutes_for_step(PacingSpeed.FORCE) < wc.minutes_for_step(PacingSpeed.FAST)
    # an unknown/None speed falls back to the SLOW (ordinary-beat) weight
    assert wc.minutes_for_step(None) == wc.minutes_for_step(PacingSpeed.SLOW)


def test_variable_flow_a_conversation_is_minutes_a_skip_is_hours():
    # 1 SLOW step ≈ a few minutes; a quiet fast-forward (FAST, 2 steps) ≈ ~an hour
    convo = wc.minutes_for_step(PacingSpeed.SLOW)
    skip = wc.minutes_for_step(PacingSpeed.FAST) * 2
    assert convo <= 5
    assert skip >= 45              # the whole point: a tick's scale isn't fixed


@pytest.mark.parametrize("minutes,phase,glyph", [
    (6 * 60, "晨", "🌅"),
    (12 * 60, "昼", "☀️"),
    (18 * 60, "暮", "🌆"),
    (22 * 60, "夜", "🌙"),
    (2 * 60, "夜", "🌙"),          # after midnight is still 夜
])
def test_time_of_day_phases(minutes, phase, glyph):
    label = wc.time_of_day(minutes)
    assert label.phase == phase and label.glyph == glyph


def test_time_of_day_wraps_past_one_day():
    # day 2, 06:30 → still 晨, and the hour resets
    minutes = 1440 + 6 * 60 + 30
    label = wc.time_of_day(minutes)
    assert label.phase == "晨" and label.hour == 6


def test_time_phrase_is_natural_prose():
    assert wc.time_phrase(7 * 60) == "清晨"
    assert wc.time_phrase(13 * 60) == "白天"
    assert wc.time_phrase(18 * 60) == "黄昏时分"
    assert wc.time_phrase(23 * 60) == "夜里"


def test_phase_transition_line_for_adjacent_and_skipped_changes():
    assert wc.phase_transition_line("昼", "暮") and "暗" in wc.phase_transition_line("昼", "暮")
    assert wc.phase_transition_line("暮", "夜") and "夜" in wc.phase_transition_line("暮", "夜")
    # a big skip can jump a phase (昼→夜) — still yields an "arrived" line, not None
    assert wc.phase_transition_line("昼", "夜") is not None
    # no change → no line
    assert wc.phase_transition_line("昼", "昼") is None


def test_clock_label_shows_day_and_hhmm():
    assert wc.clock_label(6 * 60 + 30) == "第1天 06:30"
    assert wc.clock_label(1440 + 18 * 60 + 5) == "第2天 18:05"


def test_opening_time_accepts_bare_phase_words_and_prose_forms():
    # a pack author's natural "夜里"/"白天" must resolve, not silently fall to 08:00
    assert wc.time_of_day(wc.parse_opening_time("夜里")).phase == "夜"
    assert wc.time_of_day(wc.parse_opening_time("白天")).phase == "昼"
    assert wc.time_of_day(wc.parse_opening_time("夜")).phase == "夜"
    assert wc.parse_opening_time("白天") != wc.DEFAULT_OPENING_MINUTES
    # time_phrase output round-trips back to the same phase
    for clk in (7 * 60, 13 * 60, 18 * 60, 23 * 60):
        phase = wc.time_of_day(clk).phase
        assert wc.time_of_day(wc.parse_opening_time(wc.time_phrase(clk))).phase == phase


def test_skip_narrates_the_boundary_it_fast_forwards_over(tmp_path):
    # /skip to nightfall should still emit the crossing line it skips past (the
    # /skip path used to bypass _emit_environment_transition).
    from verisaria.runtime.session import GameSession
    from verisaria import protocol as P
    g = GameSession("fixtures/content_packs/frostgate_watchpost.json",
                    save_dir=str(tmp_path), llm_backend="fake")
    # isolate the player so the area is quiet and /skip actually fast-forwards
    for e in g.world.state.entities.values():
        if e.entity_id != g.player_id:
            e.location_id = "__away__"
    g.world.state.clock_minutes = 19 * 60 + 30   # 19:30 (暮); +6h skip → 01:30 (夜)
    events: list = []
    g._event_sink = events.append
    g._handle_skip()
    narrations = [e for e in events if isinstance(e, P.Narration)]
    assert any(("夜" in e.text or "暗" in e.text) for e in narrations)


def test_parse_opening_time_accepts_hhmm_and_named_phases():
    assert wc.parse_opening_time("18:30") == 18 * 60 + 30
    assert wc.parse_opening_time("06:00") == 6 * 60
    # a named phase maps to a representative hour
    assert wc.time_of_day(wc.parse_opening_time("黄昏")).phase == "暮"
    assert wc.time_of_day(wc.parse_opening_time("清晨")).phase == "晨"
    # unknown / missing → default opening (08:00)
    assert wc.parse_opening_time(None) == wc.DEFAULT_OPENING_MINUTES == 8 * 60
    assert wc.parse_opening_time("nonsense") == wc.DEFAULT_OPENING_MINUTES


# -- wired into the engine --

def test_tick_advance_moves_clock_by_minutes():
    from verisaria.engine.world import WorldCore, WorldState
    w = WorldCore(initial_state=WorldState())
    start = w.state.clock_minutes
    w.tick_advance(7)
    assert w.state.tick == 1 and w.state.clock_minutes == start + 7
    w.tick_advance()  # default = an ordinary SLOW beat, not a fixed constant
    assert w.state.clock_minutes == start + 7 + wc.minutes_for_step(None)


def test_session_wait_advances_world_clock(tmp_path):
    from verisaria.runtime.session import GameSession
    g = GameSession("fixtures/content_packs/frostgate_watchpost.json",
                    save_dir=str(tmp_path), llm_backend="fake")
    before = g.world.state.clock_minutes
    g.run_tick("")  # the player waits one beat → in-world time moves on
    assert g.world.state.clock_minutes > before


def test_tick_crossing_dusk_emits_ambient_narration(tmp_path):
    # slice 3b: passing a time-of-day boundary surfaces a short ambient line so the
    # prose isn't mute to "天黑了" (the status bar isn't the only signal).
    from verisaria.runtime.session import GameSession
    from verisaria import protocol as P
    g = GameSession("fixtures/content_packs/frostgate_watchpost.json",
                    save_dir=str(tmp_path), llm_backend="fake")
    events: list = []
    g._event_sink = events.append
    g.world.state.clock_minutes = 17 * 60 - 1   # 16:59 (昼), one beat tips into 暮
    g.run_tick("")
    narrations = [e for e in events if isinstance(e, P.Narration)]
    assert any(("暗" in e.text or "向晚" in e.text or "夜" in e.text) for e in narrations)


def test_pack_opening_time_sets_starting_clock(tmp_path, monkeypatch):
    # a pack that opens at dusk starts the clock there rather than the 08:00 default
    from verisaria.runtime.session import GameSession
    from verisaria.engine.campaign_loader import CampaignLoader
    real = CampaignLoader.load_or_fallback  # bound classmethod

    def _wrapped(path):
        pack, state, validation = real(path)
        pack.world_premise.opening_time = "黄昏"
        return pack, state, validation

    monkeypatch.setattr(CampaignLoader, "load_or_fallback", staticmethod(_wrapped))
    g = GameSession("fixtures/content_packs/frostgate_watchpost.json",
                    save_dir=str(tmp_path), llm_backend="fake")
    assert wc.time_of_day(g.world.state.clock_minutes).phase == "暮"
