"""Engine ↔ frontend protocol (Step 2) — the typed, serializable contract.

Commands go in, Events come out (the per-tick stream), and a pull-able
``WorldSnapshot`` renders the panes. This is the **A5 authorization boundary**:
only player-perceivable data crosses it — never NPC private memory, hidden
world-book entries, or god's-eye state. Python frontends (CLI/TUI) use these
dataclasses directly; a non-Python client (e.g. Godot) consumes the same shapes
as JSON over IPC.

See docs/design/protocol-design.md. Pure data — no engine behaviour here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Relationship rendering (decision §8.1)
#
# The protocol carries the DATA — raw value + qualitative band + after-name
# phrase. The frontend's verbosity setting (simple/normal/verbose) decides what
# to show. The band→phrase mapping lives here so every frontend (incl. Godot)
# stays consistent instead of re-deriving it.
# ---------------------------------------------------------------------------

REL_DIM_LABELS: dict[str, str] = {
    "trust": "信任", "suspicion": "怀疑", "fear": "恐惧",
    "affection": "好感", "respect": "敬重", "familiarity": "熟悉",
}

# Calibrated to this engine's value distribution (diminishing-returns [0,1];
# from playtests ~0.5 is already a strong, felt stance).
def _band(value: float) -> str:
    if value < 0.1:
        return "negligible"
    if value < 0.3:
        return "slight"
    if value < 0.5:
        return "moderate"
    return "strong"


# After-name qualitative fragments, read naturally as "{name}{phrase}".
_REL_PHRASES: dict[str, dict[str, str]] = {
    "trust":       {"slight": "对你有几分信任", "moderate": "比较信任你",   "strong": "很信任你"},
    "suspicion":   {"slight": "对你有些戒备",   "moderate": "对你颇有戒心", "strong": "对你戒心很重"},
    "fear":        {"slight": "对你有些畏惧",   "moderate": "对你心怀畏惧", "strong": "很怕你"},
    "affection":   {"slight": "对你略有好感",   "moderate": "挺中意你",     "strong": "对你很有好感"},
    "respect":     {"slight": "对你略有敬意",   "moderate": "对你颇为敬重", "strong": "很敬重你"},
    "familiarity": {"slight": "对你略有印象",   "moderate": "对你渐熟",     "strong": "跟你很熟络"},
}


@dataclass(frozen=True)
class RelationshipDescriptor:
    dimension: str   # trust / suspicion / fear / affection / respect / familiarity
    value: float     # raw [0,1]
    label: str       # 中文维度名（怀疑/信任…）
    band: str        # negligible | slight | moderate | strong
    phrase: str      # after-name fragment ("" when negligible)


def relationship_descriptor(dimension: str, value: float) -> RelationshipDescriptor:
    band = _band(value)
    phrase = "" if band == "negligible" else _REL_PHRASES.get(dimension, {}).get(band, "")
    return RelationshipDescriptor(
        dimension=dimension,
        value=round(float(value), 4),
        label=REL_DIM_LABELS.get(dimension, dimension),
        band=band,
        phrase=phrase,
    )


def render_relationship(
    name: str, d: RelationshipDescriptor, verbosity: str = "normal"
) -> str:
    """Convenience renderer for the three player verbosity modes. (Sugar — a
    frontend may render the descriptor however it likes; Godot reimplements in
    GDScript. The canonical band/phrase data is what matters.)"""
    if verbosity == "simple":
        return f"{d.label} {d.value:.2f}"
    if verbosity == "verbose":
        return f"[{d.label} {d.value:.2f}] {name}{d.phrase}"
    return f"{name}{d.phrase}"  # normal


# ---------------------------------------------------------------------------
# Commands (frontend → engine) — only state-changing intents
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Command:
    """Base for all frontend→engine commands."""


@dataclass(frozen=True)
class SubmitInput(Command):
    text: str            # natural-language input (also carries clarification replies)


@dataclass(frozen=True)
class Wait(Command):
    pass


@dataclass(frozen=True)
class Skip(Command):
    ticks: int = 1


@dataclass(frozen=True)
class Save(Command):
    label: str | None = None


@dataclass(frozen=True)
class Load(Command):
    save_id: str = ""


_COMMANDS: dict[str, type[Command]] = {
    c.__name__: c for c in (SubmitInput, Wait, Skip, Save, Load)
}


def command_to_dict(cmd: Command) -> dict[str, Any]:
    return {"command": type(cmd).__name__, **asdict(cmd)}


def command_from_dict(d: dict[str, Any]) -> Command:
    d = dict(d)
    name = d.pop("command")
    return _COMMANDS[name](**d)


# ---------------------------------------------------------------------------
# Events (engine → frontend) — the per-tick stream
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Event:
    """Base for all engine→frontend events. ``tick`` is when it occurred."""
    tick: int


# — dialogue —
@dataclass(frozen=True)
class Progress(Event):
    message: str                    # "正在领会你的意思…"


@dataclass(frozen=True)
class SpeechToken(Event):           # opt-in streaming (decision §8.2)
    npc_id: str
    token: str


@dataclass(frozen=True)
class NpcSpoke(Event):
    npc_id: str
    name: str
    line: str


@dataclass(frozen=True)
class PlayerSpoke(Event):
    line: str


# — world —
@dataclass(frozen=True)
class PlayerMoved(Event):
    from_loc: str
    to_loc: str


@dataclass(frozen=True)
class NpcMoved(Event):              # only when player-perceivable (A5)
    npc_id: str
    from_loc: str
    to_loc: str


@dataclass(frozen=True)
class Narration(Event):
    text: str                       # the one engine-authored prose that crosses


@dataclass(frozen=True)
class PressureEvent(Event):
    event_type: str
    source: str
    summary: str


# — consequence channels (PLAY-3) —
@dataclass(frozen=True)
class RelationshipShifted(Event):   # Channel A
    npc_id: str
    name: str
    descriptor: RelationshipDescriptor   # the NPC's stance AFTER the shift
    delta: float = 0.0                   # the change this tick (the "consequence")


@dataclass(frozen=True)
class StanceConfirmed(Event):       # Channel B
    topic_id: str
    label: str


@dataclass(frozen=True)
class WorldVarChanged(Event):       # Channel C
    var_id: str
    label: str
    value: Any


# — flow —
@dataclass(frozen=True)
class ClarificationNeeded(Event):
    question: str
    options: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TickAdvanced(Event):
    new_tick: int


@dataclass(frozen=True)
class Notice(Event):
    text: str   # engine feedback when a turn produced no in-world events
                # (parse failed / can't do that / clarification re-prompt)


@dataclass(frozen=True)
class Error(Event):
    message: str


def event_to_dict(e: Event) -> dict[str, Any]:
    """Tagged, JSON-clean dict (nested DTOs serialize recursively)."""
    return {"event": type(e).__name__, **asdict(e)}


# ---------------------------------------------------------------------------
# Snapshot (engine → frontend) — pull-able, player-perceivable state for panes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PresentEntity:
    id: str
    name: str
    type: str            # "npc" | "player" | ...


@dataclass(frozen=True)
class LocationView:
    id: str
    name: str = ""          # display name (falls back to id)
    description: str = ""


@dataclass(frozen=True)
class PlayerView:
    hp: int = 0
    max_hp: int = 0
    stamina: int = 0
    traits: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RelationshipView:
    npc_id: str
    name: str
    descriptors: list[RelationshipDescriptor] = field(default_factory=list)


@dataclass(frozen=True)
class WorldVarView:
    var_id: str
    label: str
    value: Any


@dataclass(frozen=True)
class AgendaView:
    drives: list[str] = field(default_factory=list)
    confirmed_stances: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MapView:
    locations: list[str] = field(default_factory=list)
    connections: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class WorldSnapshot:
    tick: int
    pacing: str
    location: LocationView
    present: list[PresentEntity] = field(default_factory=list)
    player: PlayerView | None = None
    relationships: list[RelationshipView] = field(default_factory=list)
    world_vars: list[WorldVarView] = field(default_factory=list)
    agenda: AgendaView | None = None
    map: MapView | None = None


def snapshot_to_dict(s: WorldSnapshot) -> dict[str, Any]:
    return asdict(s)


# ---------------------------------------------------------------------------
# The result of submitting one Command
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TickResult:
    """What ``EngineSession.submit`` returns: the events emitted this turn, the
    updated player-perceivable snapshot, and (transitionally) the legacy narrative
    string while the CLI migrates off it."""
    events: list[Event] = field(default_factory=list)
    snapshot: WorldSnapshot | None = None
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": [event_to_dict(e) for e in self.events],
            "snapshot": snapshot_to_dict(self.snapshot) if self.snapshot else None,
            "text": self.text,
        }
