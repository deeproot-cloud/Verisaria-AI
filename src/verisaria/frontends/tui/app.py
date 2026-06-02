"""Verisaria TUI — a Textual app over the EngineSession protocol.

v1 = core loop (status + event-log + input, worker-threaded streaming tick).
v2 = right sidebar (nearby NPCs + relationships, world state) from the snapshot,
     plus inline consequence events (world/relationship/stance) in the stream.
Side map/focus panels come in v3 — see docs/design/tui-design.md.
"""
from __future__ import annotations

import logging
import time

from rich.text import Text

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, RichLog, Static

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
    #events { width: 2fr; border: round $primary-darken-2; padding: 0 1; }
    #sidebar { width: 36; }
    #nearby { height: 1fr; border: round $primary-darken-2; padding: 0 1; }
    #world { height: auto; border: round $primary-darken-2; padding: 0 1; }
    #input { dock: bottom; }
    """
    TITLE = "Verisaria"

    def __init__(self, engine: EngineSession) -> None:
        super().__init__()
        self.engine = engine
        self._busy = False
        self._n_events = 0

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        with Horizontal(id="main"):
            yield RichLog(id="events", markup=False, wrap=True, highlight=False)
            with Vertical(id="sidebar"):
                yield Static(id="nearby")
                yield Static(id="world")
        yield Input(id="input", placeholder=_PROMPT)

    def on_mount(self) -> None:
        self.query_one("#events", RichLog).border_title = "事件流"
        self.query_one("#nearby", Static).border_title = "附近 NPC"
        self.query_one("#world", Static).border_title = "世界状态"
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
        markup = R.render_event(ev)
        if markup:
            self._log(markup)

    def _log(self, markup: str) -> None:
        self.query_one("#events", RichLog).write(_m(markup))

    def _refresh_panels(self, snap: P.WorldSnapshot) -> None:
        self.query_one("#status", Static).update(_m(R.render_status(snap)))
        self.query_one("#nearby", Static).update(_m(R.render_nearby(snap)))
        self.query_one("#world", Static).update(_m(R.render_world(snap)))

    def _finish_tick(self) -> None:
        self._busy = False
        inp = self.query_one("#input", Input)
        inp.disabled = False
        inp.placeholder = _PROMPT
        inp.focus()
