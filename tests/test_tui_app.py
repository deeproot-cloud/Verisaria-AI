"""TUI app smoke test: input → worker-threaded tick streams events → status advances.
Driven via asyncio.run (no pytest-asyncio dependency)."""
from __future__ import annotations

import asyncio

from textual.widgets import Input

from verisaria.protocol.engine_session import EngineSession
from verisaria.frontends.tui.app import VerisariaApp
from verisaria import protocol as P
from verisaria.engine.schemas import ParsedIntent, ActionType, CommitmentLevel

PACK = "fixtures/content_packs/frostgate_watchpost.json"


def test_tui_submit_streams_events_and_advances_tick(tmp_path):
    es = EngineSession.start(PACK, save_dir=str(tmp_path), llm_backend="fake")
    es.game.intent_parser.parse = lambda raw_text, **kw: ParsedIntent(
        intent_id="i", source="natural_language", raw_text=raw_text,
        intent_type=ActionType.SPEECH, actor_id="player_001",
        target_id="npc.captain_brann", content="你好，队长。", modifiers={},
        commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content=raw_text, timestamp=0,
    )
    app = VerisariaApp(es)
    seen: list = []
    snaps: list = []  # snapshots the app refreshed its panels with

    async def scenario():
        async with app.run_test() as pilot:
            on_ev, refresh = app._on_event, app._refresh_panels
            app._on_event = lambda ev: (seen.append(ev), on_ev(ev))
            app._refresh_panels = lambda s: (snaps.append(s), refresh(s))
            app.query_one("#input", Input).value = "对队长布兰说：你好。"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app._busy is False  # input re-enabled after the tick

    asyncio.run(scenario())

    assert any(isinstance(e, P.PlayerSpoke) for e in seen)
    assert any(isinstance(e, P.TickAdvanced) for e in seen)
    # the app refreshed its sidebar panels with a post-tick snapshot carrying the
    # world var + the co-located NPCs (what render_nearby / render_world draw).
    last = snaps[-1]
    assert any(w.var_id == "refugees_admitted" for w in last.world_vars)
    assert any(e.name in ("队长布兰", "哨兵伏斯") for e in last.present)


def test_tui_left_column_panels_populate_and_quit_bound(tmp_path):
    """v3: the left column (map + agenda) mounts and renders the starting
    location/topology, and Ctrl+Q is wired to quit (shown in the Footer)."""
    from verisaria.frontends.tui import render as R

    es = EngineSession.start(PACK, save_dir=str(tmp_path), llm_backend="fake")
    app = VerisariaApp(es)
    captured: dict = {}

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            # the left-column widgets exist and got a refresh snapshot
            captured["map_widget"] = app.query_one("#map")
            captured["agenda_widget"] = app.query_one("#agenda")
            captured["footer"] = [w for w in app.query("*")
                                  if type(w).__name__ == "Footer"]
            captured["snap"] = es.snapshot()

    asyncio.run(scenario())

    assert captured["map_widget"] is not None and captured["agenda_widget"] is not None
    assert len(captured["footer"]) == 1  # Footer mounted (surfaces ^q 退出)
    # the snapshot the app renders carries the topology; render_map marks 门楼 ★
    map_markup = R.render_map(captured["snap"])
    assert "★ 门楼" in map_markup
    assert any(b.action == "quit" and "ctrl+q" in b.key
               for b in VerisariaApp.BINDINGS)


def test_tui_typewriter_accumulates_then_commits(tmp_path):
    """SpeechToken events grow the live line (with a cursor); the committing
    NpcSpoke clears it and writes the finished reply to the event log."""
    from textual.widgets import Static

    es = EngineSession.start(PACK, save_dir=str(tmp_path), llm_backend="fake")
    app = VerisariaApp(es)
    live_updates: list[str] = []
    logged: list[str] = []

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            app._names = {"npc.captain_brann": "队长布兰"}
            live = app.query_one("#liveline", Static)
            orig_update = live.update
            live.update = lambda r="": (live_updates.append(str(r)), orig_update(r))[1]
            orig_log = app._log
            app._log = lambda m: (logged.append(str(m)), orig_log(m))[1]

            for tok in ["你", "说", "得", "在理"]:
                app._on_event(P.SpeechToken(tick=1, npc_id="npc.captain_brann", token=tok))
            assert app._stream_buf["npc.captain_brann"] == "你说得在理"
            # the live line grew with the accumulated reply + a typewriter cursor
            assert any("你说得在理" in u and "▌" in u for u in live_updates)
            # SpeechTokens don't hit the event log (they're on the live line only)
            assert not logged

            app._on_event(P.NpcSpoke(
                tick=1, npc_id="npc.captain_brann", name="队长布兰", line="你说得在理"))
            assert "npc.captain_brann" not in app._stream_buf  # buffer cleared
            assert live_updates[-1] == ""                       # live line cleared
            assert any("你说得在理" in m for m in logged)        # committed to log

    asyncio.run(scenario())


def test_tui_responsive_collapses_secondary_panels(tmp_path):
    """On a wide terminal all panels show; as it narrows the left column drops
    first, then the right sidebar — but the centre event log never collapses."""
    def _displays(size):
        es = EngineSession.start(PACK, save_dir=str(tmp_path), llm_backend="fake")
        app = VerisariaApp(es)
        out: dict = {}

        async def scenario():
            async with app.run_test(size=size) as pilot:
                await pilot.pause()
                out["left"] = app.query_one("#left").display
                out["side"] = app.query_one("#sidebar").display
                out["events"] = app.query_one("#events").display

        asyncio.run(scenario())
        return out

    wide = _displays((130, 40))
    assert wide["left"] and wide["side"] and wide["events"]

    mid = _displays((95, 40))                 # left dropped, sidebar kept
    assert not mid["left"] and mid["side"] and mid["events"]

    narrow = _displays((70, 40))              # both secondary dropped
    assert not narrow["left"] and not narrow["side"]
    assert narrow["events"]                   # core loop survives


def test_tui_god_view_toggles_left_column(tmp_path):
    """Ctrl+G swaps the left column (map+agenda) for the DEBUG god-view, which
    renders each co-located NPC's real state; toggling again restores it."""
    es = EngineSession.start(PACK, save_dir=str(tmp_path), llm_backend="fake")
    app = VerisariaApp(es)
    seen_ids: list[str] = []
    orig = es.debug_god_view
    es.debug_god_view = lambda nid: (seen_ids.append(nid), orig(nid))[1]

    state: dict = {}

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._god is False
            assert app.query_one("#godview").display is False
            await pilot.press("ctrl+g")
            await pilot.pause()
            state["god_on"] = app._god
            state["godview_shown"] = app.query_one("#godview").display
            state["map_hidden"] = app.query_one("#map").display
            await pilot.press("ctrl+g")
            await pilot.pause()
            state["god_off"] = app._god
            state["map_back"] = app.query_one("#map").display

    asyncio.run(scenario())

    assert state["god_on"] is True and state["godview_shown"] is True
    assert state["map_hidden"] is False              # map hidden while god-view up
    assert state["god_off"] is False and state["map_back"] is True
    # the god-view queried real state for the co-located NPCs (watch + refugees etc.)
    assert any(nid.startswith("npc.") for nid in seen_ids)


def test_tui_run_log_captures_command_events_and_timing(tmp_path):
    """--log writes a trace: the submitted command, each event, and tick timing —
    so a session's problems are diagnosable after the fact."""
    import logging

    records: list[str] = []

    class _ListHandler(logging.Handler):
        def emit(self, rec): records.append(rec.getMessage())

    logger = logging.getLogger("verisaria")
    handler = _ListHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    es = EngineSession.start(PACK, save_dir=str(tmp_path), llm_backend="fake")
    es.game.intent_parser.parse = lambda raw_text, **kw: ParsedIntent(
        intent_id="i", source="natural_language", raw_text=raw_text,
        intent_type=ActionType.SPEECH, actor_id="player_001",
        target_id="npc.captain_brann", content="你好。", modifiers={},
        commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content=raw_text, timestamp=0,
    )
    app = VerisariaApp(es)

    async def scenario():
        async with app.run_test() as pilot:
            app.query_one("#input", Input).value = "对队长布兰说：你好。"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

    try:
        asyncio.run(scenario())
    finally:
        logger.removeHandler(handler)

    assert any("CMD input" in m for m in records)
    assert any(m.startswith("EV ") for m in records)
    assert any("tick done" in m for m in records)
