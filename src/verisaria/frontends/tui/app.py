"""Verisaria TUI — a Textual app over the EngineSession protocol.

v1 = core loop (status + event-log + input, worker-threaded streaming tick).
v2 = right sidebar (nearby NPCs + world) + inline consequence events.
v3 = left column (topology map + agenda), Footer + Ctrl+Q quit, solid stance bars.
See docs/design/tui-design.md.
"""
from __future__ import annotations

import logging
import time

from rich.text import Text

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Input, RichLog, Static

from verisaria import protocol as P
from verisaria.protocol.engine_session import EngineSession
from verisaria.frontends.tui import render as R

_PROMPT = "说点什么，或做点什么…（回车提交）"
log = logging.getLogger("verisaria.tui")  # silent unless a handler is attached (--log)


def _m(markup: str) -> Text:
    """Parse our Rich-markup strings into Text once, at the widget boundary."""
    return Text.from_markup(markup)


class VerisariaApp(App):
    CSS = """
    Screen { layout: vertical; }
    #status { height: 1; background: $panel; padding: 0 1; }
    #main { height: 1fr; }
    #left { width: 30; }
    #map { height: 1fr; border: round $primary-darken-2; padding: 0 1; }
    #agenda { height: 1fr; border: round $primary-darken-2; padding: 0 1; }
    #godview { height: 1fr; border: round #b65fb6; padding: 0 1; display: none; }
    #center { width: 2fr; }
    #events { height: 1fr; border: round $primary-darken-2; padding: 0 1; }
    #liveline { height: auto; padding: 0 1; }
    #sidebar { width: 36; }
    #nearby { height: 1fr; border: round $primary-darken-2; padding: 0 1; }
    #world { height: auto; border: round $primary-darken-2; padding: 0 1; }
    """
    TITLE = "Verisaria"
    BINDINGS = [
        Binding("ctrl+q", "quit", "退出"),
        Binding("ctrl+c", "quit", "退出", show=False),
        Binding("ctrl+g", "toggle_god", "上帝视角"),
    ]

    def __init__(self, engine: EngineSession) -> None:
        super().__init__()
        self.engine = engine
        self._busy = False
        self._n_events = 0
        self._stream_buf: dict[str, str] = {}   # npc_id → reply so far (live line)
        self._names: dict[str, str] = {}        # npc_id → display name (from snapshot)
        self._god = False                       # DEBUG god-view toggled on?
        self._last_snap: P.WorldSnapshot | None = None

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Static(id="map")
                yield Static(id="agenda")
                yield Static(id="godview")
            with Vertical(id="center"):
                yield RichLog(id="events", markup=False, wrap=True, highlight=False)
                yield Static(id="liveline")
            with Vertical(id="sidebar"):
                yield Static(id="nearby")
                yield Static(id="world")
        yield Input(id="input", placeholder=_PROMPT)
        yield Footer()

    def on_mount(self) -> None:
        for wid, title in (
            ("#map", "地图"), ("#agenda", "议程"), ("#events", "事件流"),
            ("#nearby", "附近 NPC"), ("#world", "世界状态"),
            ("#godview", "DEBUG 上帝视角"),
        ):
            self.query_one(wid).border_title = title
        self._refresh_panels(self.engine.snapshot())
        self._log("[dim]——— Verisaria ——— 输入自然语言行动，回车提交。[/]")
        self.query_one("#input", Input).focus()

    def on_input_submitted(self, message: Input.Submitted) -> None:
        if self._busy:
            return
        text = message.value.strip()
        message.input.value = ""
        if not text:
            return
        self._busy = True
        inp = self.query_one("#input", Input)
        inp.disabled = True
        inp.placeholder = "（领会中…）"
        self._log(f"[{R.AMBER}]> {text}[/]")
        log.info("CMD input: %r", text)
        self._run_tick(text)

    # -- the slow tick runs off the UI thread; events stream back live --
    @work(thread=True, exclusive=True)
    def _run_tick(self, text: str) -> None:
        t0 = time.monotonic()
        self._n_events = 0
        try:
            snap = self.engine.submit_streaming(
                P.SubmitInput(text),
                on_event=lambda ev: self.call_from_thread(self._on_event, ev),
            )
        except Exception as exc:  # a frontend must never die on an engine hiccup
            log.exception("tick failed for input %r", text)
            self.call_from_thread(self._log, f"[{R.RED}][错误] {exc}[/]")
            snap = self.engine.snapshot()
        dt = time.monotonic() - t0
        log.info("tick done in %.1fs (%d events) @tick=%s loc=%s",
                 dt, self._n_events, snap.tick, snap.location.id)
        self.call_from_thread(self._refresh_panels, snap)
        self.call_from_thread(self._finish_tick)

    # -- main-thread UI updates --
    def _on_event(self, ev: P.Event) -> None:
        self._n_events += 1
        log.info("EV [t%s] %s", ev.tick, R.summarize_event(ev))
        # Typewriter: accumulate an addressed NPC's reply on the live line as tokens
        # stream in; the committing NpcSpoke (written to the log below) clears it.
        if isinstance(ev, P.SpeechToken):
            self._stream_append(ev.npc_id, ev.token)
            return
        if isinstance(ev, P.NpcSpoke):
            self._stream_clear(ev.npc_id)
        markup = R.render_event(ev)
        if markup:
            self._log(markup)

    def _stream_append(self, npc_id: str, token: str) -> None:
        buf = self._stream_buf.get(npc_id, "") + token
        self._stream_buf[npc_id] = buf
        name = self._names.get(npc_id, npc_id.replace("npc.", ""))
        self.query_one("#liveline", Static).update(
            _m(f"[{R.PARCHMENT}]{R._esc(name)}：{R._esc(buf)}▌[/]")
        )

    def _stream_clear(self, npc_id: str) -> None:
        if self._stream_buf.pop(npc_id, None) is not None:
            self.query_one("#liveline", Static).update("")

    def _log(self, markup: str) -> None:
        self.query_one("#events", RichLog).write(_m(markup))

    def _refresh_panels(self, snap: P.WorldSnapshot) -> None:
        # co-located names, so a streamed reply's live line can be prefixed correctly
        self._names = {e.id: e.name for e in snap.present}
        self._last_snap = snap
        self.query_one("#status", Static).update(_m(R.render_status(snap)))
        self.query_one("#map", Static).update(_m(R.render_map(snap)))
        self.query_one("#agenda", Static).update(_m(R.render_agenda(snap)))
        self.query_one("#nearby", Static).update(_m(R.render_nearby(snap)))
        self.query_one("#world", Static).update(_m(R.render_world(snap)))
        if self._god:  # keep the debug view current as the world changes
            self._refresh_godview()

    # -- DEBUG god-view: swap the left column to each co-located NPC's real state --
    def action_toggle_god(self) -> None:
        self._god = not self._god
        self.query_one("#map").display = not self._god
        self.query_one("#agenda").display = not self._god
        self.query_one("#godview").display = self._god
        if self._god:
            self._refresh_godview()

    def _refresh_godview(self) -> None:
        views = []
        if self._last_snap is not None:
            for e in self._last_snap.present:
                if e.type == "npc":
                    gv = self.engine.debug_god_view(e.id)
                    if gv is not None:
                        views.append(gv)
        self.query_one("#godview", Static).update(_m(R.render_godview(views)))

    def _finish_tick(self) -> None:
        self._busy = False
        self._stream_buf.clear()  # drop any half-streamed line (e.g. on error)
        self.query_one("#liveline", Static).update("")
        inp = self.query_one("#input", Input)
        inp.disabled = False
        inp.placeholder = _PROMPT
        inp.focus()
