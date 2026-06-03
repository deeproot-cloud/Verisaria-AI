"""Protocol-level facade (Step 2c).

``EngineSession`` wraps a ``GameSession`` and speaks the protocol: feed it a
``Command``, get back the per-tick ``Event`` stream plus a player-perceivable
``WorldSnapshot``. This is the contract a TUI / Godot frontend drives — and the
one the CLI will migrate onto. It never exposes engine internals beyond the A5
boundary the snapshot enforces.

See docs/design/protocol-design.md.
"""
from __future__ import annotations

from typing import Any, Callable

from verisaria import protocol
from verisaria.runtime.session import GameSession


class EngineSession:
    def __init__(self, game: GameSession) -> None:
        self._game = game
        self._buffer: list[protocol.Event] = []
        game._event_sink = self._buffer.append

    @classmethod
    def start(
        cls,
        content_pack_path: str,
        *,
        save_dir: str = "saves",
        llm_backend: str = "fake",
        **kwargs: Any,
    ) -> "EngineSession":
        return cls(GameSession(
            content_pack_path, save_dir=save_dir, llm_backend=llm_backend, **kwargs
        ))

    @property
    def game(self) -> GameSession:
        """Escape hatch for transitional callers (the CLI) — not for frontends."""
        return self._game

    # -- drive the engine --

    def submit(self, command: protocol.Command) -> protocol.TickResult:
        """Execute one command; return the events it emitted + a fresh snapshot."""
        self._buffer.clear()
        text = self._dispatch(command)
        events = list(self._buffer)
        self._buffer.clear()
        return protocol.TickResult(events=events, snapshot=self.snapshot(), text=text)

    def submit_streaming(
        self,
        command: protocol.Command,
        on_event: Callable[[protocol.Event], None],
    ) -> protocol.WorldSnapshot:
        """Like ``submit`` but pushes each ``Event`` to ``on_event`` **live** as it
        fires — a TUI runs this in a worker thread so a long LLM tick streams in
        instead of freezing. Returns the final snapshot; the prior buffered sink is
        restored afterward."""
        prev = self._game._event_sink
        substantive = 0

        def sink(ev: protocol.Event) -> None:
            nonlocal substantive
            if not isinstance(ev, protocol.Progress):
                substantive += 1
            on_event(ev)

        self._game._event_sink = sink
        try:
            text = self._dispatch(command)
        finally:
            self._game._event_sink = prev
        # An early-return turn (parse failed / can't do that / clarification
        # re-prompt) produces a feedback STRING but no in-world events — surface it
        # as a Notice so the frontend isn't silent (the engine's string return is
        # otherwise dropped by structured frontends).
        if substantive == 0 and text and text.strip():
            on_event(protocol.Notice(tick=self._game.world.state.tick, text=text))
        return self.snapshot()

    def _dispatch(self, command: protocol.Command) -> str:
        g = self._game
        if isinstance(command, protocol.SubmitInput):
            return g.run_tick(command.text)
        if isinstance(command, protocol.Wait):
            return g.run_tick("")
        if isinstance(command, protocol.Skip):
            text = ""
            for _ in range(max(1, command.ticks)):
                text = g.run_tick("")
            return text
        if isinstance(command, protocol.Save):
            return g._handle_command("/save") or ""
        if isinstance(command, protocol.Load):
            return g._handle_command(f"/load {command.save_id}") or ""
        raise TypeError(f"Unsupported command: {type(command).__name__}")

    # -- pull player-perceivable state for pane rendering (A5 boundary) --

    def snapshot(self) -> protocol.WorldSnapshot:
        g = self._game
        g._flush_appraisals()  # never expose stale relationship data
        state = g.world.state
        player = state.get_entity(g.player_id)
        loc_id = player.location_id if player else ""

        present = [
            protocol.PresentEntity(
                id=e.entity_id, name=state.display_name(e.entity_id), type=e.entity_type,
            )
            for e in state.entities.values()
            if e.location_id == loc_id and e.entity_id != g.player_id
        ]

        player_view = None
        if player is not None:
            player_view = protocol.PlayerView(
                hp=getattr(player, "hp", 0),
                max_hp=getattr(player, "max_hp", 0),
                stamina=getattr(player, "stamina", 0),
                traits=list(getattr(player, "traits", []) or []),
            )

        relationships = []
        for snap in g.relationship_store.relationships_toward(g.player_id):
            descriptors = [
                protocol.relationship_descriptor(dim, val)
                for dim, val in sorted(
                    snap.dimensions.items(), key=lambda kv: kv[1], reverse=True
                )
                if val > 0
            ]
            if descriptors:
                relationships.append(protocol.RelationshipView(
                    npc_id=snap.npc_id, name=state.display_name(snap.npc_id),
                    descriptors=descriptors,
                ))

        world_vars = [
            protocol.WorldVarView(
                var_id=vid, label=spec.get("label", vid),
                value=state.world_vars.get(vid, spec.get("initial")),
            )
            for vid, spec in g._world_var_specs.items()
        ]

        agenda = g.agenda_service.get_agenda(current_tick=state.tick)
        agenda_view = protocol.AgendaView(
            drives=[d.label for d in agenda.current_drives],
            confirmed_stances=list(g.agenda_service.get_confirmed_stance_topics()),
            open_questions=list(getattr(agenda, "open_questions", []) or []),
        )

        loc = state.locations.get(loc_id) if hasattr(state, "locations") else None
        location = protocol.LocationView(
            id=loc_id, name=state.location_label(loc_id),
            description=getattr(loc, "description", "") or "",
        )

        # Topology map — exits from the current location + other known places.
        exits = [
            protocol.MapExit(
                to=c.to_location, name=state.location_label(c.to_location),
                distance=getattr(c, "distance", ""),
            )
            for c in getattr(loc, "connections", []) or []
        ]
        exit_ids = {e.to for e in exits} | {loc_id}
        others = [
            state.location_label(lid) for lid in state.locations if lid not in exit_ids
        ]
        map_view = protocol.MapView(
            current=loc_id, current_name=state.location_label(loc_id),
            exits=exits, others=others,
        )

        premise = getattr(g.pack, "world_premise", None)
        central_tension = getattr(premise, "central_tension", "") or ""

        return protocol.WorldSnapshot(
            tick=state.tick,
            pacing=getattr(g, "_current_pacing", "normal"),
            location=location,
            present=present,
            player=player_view,
            relationships=relationships,
            world_vars=world_vars,
            agenda=agenda_view,
            map=map_view,
            central_tension=central_tension,
        )
