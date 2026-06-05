"""Weather — a slow, climate-gated random walk advanced by elapsed world-time
(not tick), deterministically seeded so saves replay identically. Pure functions;
see docs/design/worldclock-and-weather.md (slice 2)."""
from __future__ import annotations

import random

from verisaria.engine import weather as W


def test_every_climate_ladder_resolves_to_a_glyph():
    # each condition a climate can reach must have a status-bar glyph
    for climate, ladder in W.CLIMATE_LADDERS.items():
        for cond in ladder:
            assert W.weather_label(cond).strip(), f"{climate}:{cond} has no label"


def test_initial_weather_respects_a_valid_declared_opening():
    # a pack opening "雨" in a temperate climate is honoured…
    assert W.initial_weather("温带", "雨") == "雨"
    # …but an out-of-climate declaration (rain in a desert) falls back into the ladder
    assert W.initial_weather("干旱", "雪") in W.CLIMATE_LADDERS["干旱"]
    # no declaration → a calm opening drawn from the climate's mild end
    assert W.initial_weather("温带", None) in W.CLIMATE_LADDERS["温带"][:2]


def test_unknown_climate_falls_back_to_default():
    assert W.initial_weather("赛博朋克", None) in W.CLIMATE_LADDERS[W.DEFAULT_CLIMATE]


def test_step_stays_on_the_ladder_and_moves_at_most_one_rung():
    ladder = W.CLIMATE_LADDERS["温带"]
    for cond in ladder:
        for seed in range(20):
            nxt = W.step_weather(cond, "温带", random.Random(seed))
            assert nxt in ladder
            assert abs(ladder.index(nxt) - ladder.index(cond)) <= 1  # slow evolution


def test_step_is_deterministic_for_a_given_seed():
    a = W.step_weather("多云", "温带", random.Random(123))
    b = W.step_weather("多云", "温带", random.Random(123))
    assert a == b


def test_stable_seed_is_process_independent():
    # not Python's salted hash() — must be identical across runs for save replay
    assert W.stable_seed("frostgate") == W.stable_seed("frostgate")
    assert isinstance(W.stable_seed("frostgate"), int)


def test_weather_label_has_glyph_and_name():
    lbl = W.weather_label("雨")
    assert "雨" in lbl and any(ch for ch in lbl if ord(ch) > 0x2600)  # an emoji glyph


def test_weather_phrase_is_natural_prose():
    assert W.weather_phrase("雪") == "下着雪"
    assert W.weather_phrase("晴") == "天色晴朗"
    assert W.weather_phrase("雾") == "起了雾"


def test_weather_change_line_only_on_change():
    assert W.weather_change_line("晴", "雪") and "下着雪" in W.weather_change_line("晴", "雪")
    assert W.weather_change_line("雪", "雪") is None
    assert W.weather_change_line("", "雪") and "下着雪" in W.weather_change_line("", "雪")


# -- wired into the engine --

def _session(tmp_path):
    from verisaria.runtime.session import GameSession
    return GameSession("fixtures/content_packs/frostgate_watchpost.json",
                       save_dir=str(tmp_path), llm_backend="fake")


def test_session_starts_with_a_sky_on_its_ladder(tmp_path):
    g = _session(tmp_path)
    assert g.world.state.weather in W.CLIMATE_LADDERS[W.DEFAULT_CLIMATE]  # no climate → temperate
    assert g.world.state.weather_hour == g.world.state.clock_minutes // 60


def test_advance_weather_is_deterministic_and_replays(tmp_path):
    # the same pack + same elapsed hours must land on the same sky (save replay)
    g1 = _session(tmp_path)
    g2 = _session(tmp_path)
    g1.world.state.clock_minutes += 6 * 60   # six hours pass
    g2.world.state.clock_minutes += 6 * 60
    g1._advance_weather()
    g2._advance_weather()
    assert g1.world.state.weather == g2.world.state.weather
    assert g1.world.state.weather_hour == g2.world.state.weather_hour  # caught up to the hour


def test_advance_weather_noop_within_the_same_hour(tmp_path):
    g = _session(tmp_path)
    before = g.world.state.weather
    g.world.state.clock_minutes += 10  # still the same hour bucket
    g._advance_weather()
    assert g.world.state.weather == before


def test_weather_round_trips_through_save_load(tmp_path):
    from verisaria.engine.world import WorldCore, WorldState
    from verisaria.engine.persistence import _world_state_to_dict, _world_state_from_dict
    state = WorldState(weather="雷雨", weather_hour=42)
    restored = _world_state_from_dict(_world_state_to_dict(state))
    assert restored.weather == "雷雨" and restored.weather_hour == 42
