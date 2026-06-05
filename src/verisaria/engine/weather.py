"""Weather — a slow, climate-gated random walk over a per-climate condition
ladder, advanced by elapsed in-world time (hourly), deterministically seeded so a
save replays to the same sky.

Kept deliberately small: each climate is an ordered ladder from its mildest to its
roughest condition; weather steps at most one rung per hour (mostly stays put), so
it evolves believably without a full transition matrix. The status bar shows a
single glyph + name; richer atmosphere is the narrator's job.

Pure functions, zero engine deps; see docs/design/worldclock-and-weather.md.
"""
from __future__ import annotations

import hashlib
import random

DEFAULT_CLIMATE = "温带"

# Ordered mild → rough. A climate only ever surfaces conditions on its own ladder
# (no snow in the tropics, no thunderstorm in the arctic).
CLIMATE_LADDERS: dict[str, list[str]] = {
    "温带": ["晴", "多云", "阴", "小雨", "雨"],
    "寒带": ["晴", "多云", "阴", "小雪", "雪"],
    "热带": ["晴", "多云", "阵雨", "雷雨"],
    "干旱": ["晴", "晴", "多云", "大风"],
    "海洋": ["多云", "阴", "雾", "小雨", "雨"],
}

_GLYPHS: dict[str, str] = {
    "晴": "☀️", "多云": "⛅", "阴": "☁️",
    "小雨": "🌦️", "雨": "🌧️", "阵雨": "🌦️", "雷雨": "⛈️",
    "小雪": "🌨️", "雪": "❄️", "雾": "🌫️", "大风": "💨",
}


def _ladder_for(climate: str | None) -> list[str]:
    return CLIMATE_LADDERS.get(climate or DEFAULT_CLIMATE,
                               CLIMATE_LADDERS[DEFAULT_CLIMATE])


def weather_label(condition: str) -> str:
    """A status-bar fragment for a condition, e.g. "🌧️ 雨"."""
    glyph = _GLYPHS.get(condition, "")
    return f"{glyph} {condition}".strip()


# Natural-prose forms (for the NPC dialogue prompt + ambient narration — slice 3b).
_PHRASES: dict[str, str] = {
    "晴": "天色晴朗", "多云": "天上多云", "阴": "天色阴沉",
    "小雨": "下着小雨", "雨": "下着雨", "阵雨": "落着阵雨", "雷雨": "电闪雷鸣、暴雨倾盆",
    "小雪": "飘着小雪", "雪": "下着雪", "雾": "起了雾", "大风": "刮着大风",
}


def weather_phrase(condition: str) -> str:
    """Natural-language description of a condition for prose ("下着雪"…)."""
    return _PHRASES.get(condition, condition or "")


def weather_change_line(old: str, new: str) -> str | None:
    """An ambient narration line for a weather change, or None if unchanged."""
    if not new or new == old:
        return None
    return f"天气变了，{weather_phrase(new)}。"


def initial_weather(climate: str | None, opening: str | None) -> str:
    """The opening sky: honour a pack-declared ``opening`` if it's reachable in
    this climate; otherwise start calm (a draw from the ladder's mild end)."""
    ladder = _ladder_for(climate)
    if opening and opening in ladder:
        return opening
    return ladder[0]


def step_weather(current: str, climate: str | None, rng: random.Random) -> str:
    """One hour's evolution: a clamped ±1 random walk on the climate ladder that
    mostly stays put (slow, believable drift)."""
    ladder = _ladder_for(climate)
    try:
        i = ladder.index(current)
    except ValueError:
        i = 0  # current condition isn't on this ladder — re-anchor at the mild end
    delta = rng.choices([-1, 0, 1], weights=[3, 4, 3])[0]
    return ladder[max(0, min(len(ladder) - 1, i + delta))]


def stable_seed(text: str) -> int:
    """A process-independent seed from a string (Python's hash() is salted per run,
    which would desync a save's weather replay). Used as the base for hourly RNG."""
    return int.from_bytes(hashlib.sha1((text or "").encode("utf-8")).digest()[:4], "big")
