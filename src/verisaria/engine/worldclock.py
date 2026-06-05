"""World clock — a real in-world time that flows at a *variable* rate per tick.

A tick is a narrative beat, not a fixed duration: a charged exchange is a couple
of minutes, a quiet「等到天亮」fast-forward is hours. The engine already grades
each beat's density via ``PacingSpeed`` (SLOW for conversation/combat, FAST/FORCE
for quiet/crisis), so we hang in-world time off that verdict — minutes-per-step
keyed on the pacing speed — instead of ``tick × constant``.

Pure functions, zero engine deps; see docs/design/worldclock-and-weather.md.
"""
from __future__ import annotations

from dataclasses import dataclass

from verisaria.engine.schemas import PacingSpeed

MINUTES_PER_DAY = 24 * 60
DEFAULT_OPENING_MINUTES = 8 * 60  # 08:00 when a pack doesn't declare an opening time

# How many in-world minutes ONE pacing step represents. The step *count* already
# encodes acceleration (a quiet area takes more steps); this weight makes each
# step's duration match the beat — so a conversation tick is minutes and a quiet
# fast-forward tick is the better part of an hour.
_MINUTES_PER_STEP: dict[PacingSpeed, int] = {
    PacingSpeed.PAUSE: 1,    # combat — life-or-death, time barely moves
    PacingSpeed.SLOW: 3,     # conversation / ordinary beat (the default)
    PacingSpeed.FORCE: 12,   # campaign-pressure event — compressed but moving
    PacingSpeed.FAST: 30,    # quiet area / explicit skip — time drifts on
}


def minutes_for_step(speed: PacingSpeed | None) -> int:
    """In-world minutes for one tick step at this pacing speed. An unknown/None
    speed falls back to the SLOW (ordinary-beat) weight."""
    return _MINUTES_PER_STEP.get(speed, _MINUTES_PER_STEP[PacingSpeed.SLOW])


# Time-of-day bands (local hour → phase). Kept to four legible phases for the
# status bar; finer prose framing is the narrator's job, not the clock's.
@dataclass(frozen=True)
class TimeOfDay:
    phase: str   # 晨 / 昼 / 暮 / 夜
    glyph: str   # 🌅 / ☀️ / 🌆 / 🌙
    hour: int    # local hour 0–23 (for callers that want the raw value)

    @property
    def label(self) -> str:
        return f"{self.glyph} {self.phase}"


def _phase_for_hour(hour: int) -> tuple[str, str]:
    if 5 <= hour < 10:
        return "晨", "🌅"
    if 10 <= hour < 17:
        return "昼", "☀️"
    if 17 <= hour < 20:
        return "暮", "🌆"
    return "夜", "🌙"  # 20:00–04:59


def time_of_day(clock_minutes: int) -> TimeOfDay:
    """Derive the time-of-day phase from the absolute world clock (minutes since
    the campaign's opening moment; may exceed a day)."""
    hour = (int(clock_minutes) // 60) % 24
    phase, glyph = _phase_for_hour(hour)
    return TimeOfDay(phase=phase, glyph=glyph, hour=hour)


# Natural-prose forms (for the NPC dialogue prompt + ambient narration — slice 3b),
# distinct from the status-bar glyph label.
_PHASE_PHRASE = {"晨": "清晨", "昼": "白天", "暮": "黄昏时分", "夜": "夜里"}
# Notable phase transitions → an ambient line; a phase change with no exact entry
# (e.g. a big skip jumps 昼→夜) falls back to the "arrived at" line.
_PHASE_TRANSITION = {
    ("昼", "暮"): "天色渐渐暗了下来。",
    ("暮", "夜"): "夜幕四合，天黑透了。",
    ("夜", "晨"): "天蒙蒙亮，又是一日。",
    ("晨", "昼"): "日头升高，到了白天。",
}
_PHASE_ARRIVE = {"晨": "天亮了。", "昼": "到了白天。", "暮": "天色向晚。", "夜": "入夜了。"}


def time_phrase(clock_minutes: int) -> str:
    """Natural-language time of day for prose ("清晨"/"白天"/"黄昏时分"/"夜里")."""
    return _PHASE_PHRASE.get(time_of_day(clock_minutes).phase, "")


def phase_transition_line(old_phase: str, new_phase: str) -> str | None:
    """An ambient narration line for a time-of-day change, or None if unchanged."""
    if not new_phase or new_phase == old_phase:
        return None
    return _PHASE_TRANSITION.get((old_phase, new_phase)) or _PHASE_ARRIVE.get(new_phase)


def clock_label(clock_minutes: int) -> str:
    """A compact「第N天 HH:MM」label for the status bar."""
    m = int(clock_minutes)
    day = m // MINUTES_PER_DAY + 1
    hh = (m // 60) % 24
    mm = m % 60
    return f"第{day}天 {hh:02d}:{mm:02d}"


# Named opening phases a pack may use instead of an explicit HH:MM — each maps to
# a representative hour inside its band.
_NAMED_OPENINGS: dict[str, int] = {
    "拂晓": 5, "黎明": 5, "清晨": 7, "早晨": 7, "早上": 7,
    "上午": 9, "正午": 12, "中午": 12, "午后": 14, "下午": 15,
    "傍晚": 18, "黄昏": 18, "日暮": 18, "夜晚": 21, "晚上": 21,
    "深夜": 1, "午夜": 0, "凌晨": 3,
    # Bare phase words + the prose forms time_phrase() emits, so a pack author's
    # natural "夜里"/"白天" resolves (and the label round-trips) instead of silently
    # falling back to 08:00.
    "晨": 7, "昼": 12, "暮": 18, "夜": 22,
    "白天": 12, "白昼": 12, "黄昏时分": 18, "夜里": 22,
}


def parse_opening_time(value: object) -> int:
    """Parse a pack's declared opening time into absolute clock minutes.
    Accepts "HH:MM", a bare hour, or a named phase (黄昏/清晨/…); anything
    unrecognized (incl. None) falls back to the default opening (08:00)."""
    if value is None:
        return DEFAULT_OPENING_MINUTES
    if isinstance(value, (int, float)):
        h = int(value)
        return (h % 24) * 60 if 0 <= h <= 23 else DEFAULT_OPENING_MINUTES
    text = str(value).strip()
    if not text:
        return DEFAULT_OPENING_MINUTES
    if ":" in text:
        hh, _, mm = text.partition(":")
        try:
            h, m = int(hh), int(mm)
        except ValueError:
            return DEFAULT_OPENING_MINUTES
        if 0 <= h <= 23 and 0 <= m < 60:
            return h * 60 + m
        return DEFAULT_OPENING_MINUTES
    if text in _NAMED_OPENINGS:
        return _NAMED_OPENINGS[text] * 60
    if text.isdigit():
        h = int(text)
        if 0 <= h <= 23:
            return h * 60
    return DEFAULT_OPENING_MINUTES
