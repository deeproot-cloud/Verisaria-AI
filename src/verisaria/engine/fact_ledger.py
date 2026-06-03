"""Emergent fact ledger — intermediate facts the LLM arbiter established in-fiction.

See docs/design/emergent-fact-ledger.md. Terminal world flags still flip only on
``success``; this remembers the concessions/conditions a ``partial_success``
ruling established (LLM-authored text), keyed by the world var they concern, so
later arbitration can build on them instead of restarting from zero state.

Invisible plumbing: the ledger only ever feeds the arbiter prompt — it emits no
protocol events and surfaces in no player-facing panel (a visible "conditions
2/3" tracker would reintroduce the quest-feel we're avoiding).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EstablishedFact:
    text: str        # LLM-authored objective statement of what was established
    regarding: str   # the world_var id this concession concerns
    npc_id: str      # the authority NPC who made it
    tick: int        # when it was established


class FactLedger:
    """A small append-with-dedup store of established facts, keyed by world var."""

    def __init__(self) -> None:
        self._facts: list[EstablishedFact] = []

    def add(self, text: str, regarding: str, npc_id: str, tick: int) -> None:
        """Record a fact. Same var + identical text refreshes its tick rather than
        duplicating (so re-stating a standing condition doesn't bloat the ledger)."""
        text = (text or "").strip()
        if not text:
            return
        fact = EstablishedFact(text=text, regarding=regarding, npc_id=npc_id, tick=tick)
        for i, f in enumerate(self._facts):
            if f.regarding == regarding and f.text == text:
                self._facts[i] = fact
                return
        self._facts.append(fact)

    def relevant(self, regarding: str, limit: int = 6) -> list[EstablishedFact]:
        """Facts about a given world var, most-recent first (bounded by var, then
        capped so the arbiter prompt can't bloat)."""
        facts = [f for f in self._facts if f.regarding == regarding]
        facts.sort(key=lambda f: f.tick, reverse=True)
        return facts[:limit]

    def all(self) -> list[EstablishedFact]:
        return list(self._facts)

    # -- persistence (rides in the save blob) --

    def get_state(self) -> list[dict[str, Any]]:
        return [
            {"text": f.text, "regarding": f.regarding, "npc_id": f.npc_id, "tick": f.tick}
            for f in self._facts
        ]

    def load_state(self, state: list[dict[str, Any]] | None) -> None:
        self._facts = [
            EstablishedFact(
                text=d.get("text", ""), regarding=d.get("regarding", ""),
                npc_id=d.get("npc_id", ""), tick=int(d.get("tick", 0)),
            )
            for d in (state or [])
        ]
