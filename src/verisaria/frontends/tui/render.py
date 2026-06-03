"""Render protocol Events into Rich markup for the TUI event log.

Pure functions (no Textual import) so they're unit-testable. Colors follow the
locked legend in docs/design/tui-design.md §4:
  player/focus = amber · NPC/narration = parchment · pressure/danger = red ·
  positive/world-change = green · ambient/secondary = dim · DEBUG = magenta.
"""
from __future__ import annotations

from verisaria import protocol as P

# Color legend (hex so it reads the same across terminals that support truecolor).
AMBER = "#d7a86e"      # player speech / current focus
PARCHMENT = "#cfc6b8"  # NPC speech / narration
RED = "#c0504d"        # pressure / tension / danger / error
GREEN = "#7faa6e"      # positive change / world-fact flip
MAGENTA = "#b65fb6"    # DEBUG / god-view (out-of-world)


def _esc(text: str) -> str:
    """Escape Rich markup so NPC/player content can't inject tags."""
    return (text or "").replace("[", "\\[")


def render_event(ev: P.Event) -> str | None:
    """Rich markup for one event, or None if it shouldn't appear in the log
    (control events like TickAdvanced / streaming tokens)."""
    tick = f"[dim]\\[{ev.tick}][/] "

    if isinstance(ev, P.Progress):
        return f"[dim]{_esc(ev.message)}[/]"
    if isinstance(ev, P.PlayerSpoke):
        return f"{tick}[{AMBER}]你：{_esc(ev.line)}[/]"
    if isinstance(ev, P.NpcSpoke):
        return f"{tick}[{PARCHMENT}]{_esc(ev.name)}：{_esc(ev.line)}[/]"
    if isinstance(ev, P.Narration):
        # Movement / look / ambient prose (the engine strips speech from this event,
        # since granular Player/Npc Spoke events already carry the dialogue).
        return f"{tick}[{PARCHMENT} italic]{_esc(ev.text)}[/]"
    if isinstance(ev, P.PlayerMoved):
        return f"{tick}[dim]你 → {_esc(ev.to_loc)}[/]"
    if isinstance(ev, P.NpcMoved):
        return f"{tick}[dim]{_esc(ev.npc_id.replace('npc.', ''))} → {_esc(ev.to_loc)}[/]"
    if isinstance(ev, P.PressureEvent):
        return f"{tick}[{RED}](压力) {_esc(ev.summary)}[/]"
    if isinstance(ev, P.WorldVarChanged):
        flag = "✓" if ev.value else "✗"
        return f"{tick}[{GREEN}]⟳ 世界变化：{_esc(ev.label)} → {flag}[/]"
    if isinstance(ev, P.RelationshipShifted):
        d = ev.descriptor
        # Colour by whether the shift is good FOR THE PLAYER: a positive-valence
        # dimension rising (or a negative one falling) is green; the reverse is red.
        positive_dim = d.dimension in ("trust", "affection", "respect", "familiarity")
        good = positive_dim == (ev.delta >= 0)
        color = GREEN if good else RED
        sign = "+" if ev.delta >= 0 else ""
        return f"{tick}[{color}]关系：{_esc(ev.name)} {d.label} {sign}{ev.delta:.2f}[/]"
    if isinstance(ev, P.StanceConfirmed):
        return f"{tick}[{AMBER}]◆ 已确认目标：{_esc(ev.label)}[/]"
    if isinstance(ev, P.ClarificationNeeded):
        opts = "   ".join(f"{i}) {_esc(o)}" for i, o in enumerate(ev.options, 1))
        tail = f"\n[dim]{opts}[/]" if opts else ""
        return f"{tick}[{RED}]{_esc(ev.question)}[/]{tail}"
    if isinstance(ev, P.Notice):
        return f"[dim]· {_esc(ev.text)}[/]"
    if isinstance(ev, P.Error):
        return f"{tick}[{RED}][错误] {_esc(ev.message)}[/]"
    # TickAdvanced, SpeechToken: not shown as log lines.
    return None


def summarize_event(ev: P.Event) -> str:
    """A compact, plain-text one-liner for the run log (no markup)."""
    name = type(ev).__name__
    if isinstance(ev, P.RelationshipShifted):
        return f"{name} {ev.name}: {ev.descriptor.label} {ev.delta:+.2f}"
    for attr in ("line", "message", "text", "summary", "question", "label",
                 "to_loc", "value", "token"):
        v = getattr(ev, attr, None)
        if v not in (None, ""):
            who = getattr(ev, "name", getattr(ev, "npc_id", getattr(ev, "var_id", "")))
            who = f" {who}" if who else ""
            return f"{name}{who}: {v}"
    return name


def _bar(value: float, width: int = 10) -> str:
    """A 0..1 gauge: █ filled (solid, legible), ░ empty."""
    filled = max(0, min(width, round(value * width)))
    return "█" * filled + "░" * (width - filled)


_DISTANCE_CN = {"adjacent": "相邻", "near": "附近", "far": "远"}


def render_map(snapshot: P.WorldSnapshot) -> str:
    """Left panel — topology: current location ★ + its exits + other known places."""
    m = snapshot.map
    if m is None:
        return "[dim]—[/]"
    lines = [f"[{AMBER}]★ {_esc(m.current_name or m.current)}[/]"]
    for ex in m.exits:
        dist = _DISTANCE_CN.get(ex.distance, ex.distance)
        tail = f"  [dim]({dist})[/]" if dist else ""
        lines.append(f"  [dim]→[/] {_esc(ex.name)}{tail}")
    if m.others:
        lines.append(f"[dim]○ 其他：{_esc('、'.join(m.others))}[/]")
    return "\n".join(lines)


def _goal_lines(agenda: P.AgendaView | None) -> list[str]:
    """The player's agenda as markup rows (confirmed stances, drives, open questions)."""
    if agenda is None:
        return []
    out: list[str] = []
    for stance in agenda.confirmed_stances:
        out.append(f"[{AMBER}]◆ {_esc(stance)}[/]")
    for drive in agenda.drives:
        out.append(f"· {_esc(drive)}")
    if agenda.open_questions:
        out.append("[dim]未解之问[/]")
        for q in agenda.open_questions:
            out.append(f"  [dim]? {_esc(q)}[/]")
    return out


def render_focus(
    snapshot: P.WorldSnapshot,
    focus_name: str | None = None,
    known: list[str] | None = None,
) -> str:
    """Left panel «处境 / 焦点», context-sensitive (tui-design §3). The DEBUG
    god-view is the *other* face of this slot (Ctrl+G).

    - Talking to an NPC (``focus_name`` set) → your goals + «你对该 NPC 的了解»:
      a digest of what you've ACTUALLY witnessed them say (A5 by construction —
      the frontend only ever held the lines it displayed).
    - Otherwise (wandering) → scene framing (where you are + central tension) +
      your goals."""
    goals = _goal_lines(snapshot.agenda)

    if focus_name:
        lines = list(goals)
        if goals:
            lines.append("")
        lines.append(f"[{AMBER}]你对{_esc(focus_name)}的了解[/]")
        if known:
            for ln in known:
                lines.append(f"  [dim]“[/]{_esc(ln)}[dim]”[/]")
        else:
            lines.append("[dim]  还没怎么打过交道。[/]")
        return "\n".join(lines)

    lines = []
    desc = (snapshot.location.description or "").strip()
    if desc:
        lines.append(f"[{PARCHMENT} italic]{_esc(desc)}[/]")
    tension = (snapshot.central_tension or "").strip()
    if tension:
        lines.append(f"[{RED}]◈ {_esc(tension)}[/]")
    if lines and goals:
        lines.append("")  # blank spacer between 处境 and 焦点
    lines.extend(goals or (["[dim]尚无明确目标。[/]"] if not lines else []))
    return "\n".join(lines) if lines else "[dim]—[/]"


def render_nearby(snapshot: P.WorldSnapshot) -> str:
    """Right panel — co-located NPCs with their dominant stance toward the player
    (dominant dimension bar + qualitative phrase; full 6 dims on expand later)."""
    rel = {r.npc_id: r for r in snapshot.relationships}
    rows: list[str] = []
    for e in snapshot.present:
        if e.type != "npc":
            continue
        line = f"[bold {PARCHMENT}]{_esc(e.name)}[/]"
        rv = rel.get(e.id)
        if rv and rv.descriptors:
            top = rv.descriptors[0]  # snapshot sorts descriptors by value desc
            line += f"\n  {top.label} {_bar(top.value)}  [dim]{_esc(top.phrase)}[/]"
        rows.append(line)
    return "\n".join(rows) if rows else "[dim]此处无人。[/]"


def render_world(snapshot: P.WorldSnapshot) -> str:
    """Right panel — mutable world facts (Channel C)."""
    if not snapshot.world_vars:
        return "[dim]—[/]"
    rows = []
    for w in snapshot.world_vars:
        mark = f"[{GREEN}]✓[/]" if w.value else f"[{RED}]✗[/]"
        rows.append(f"{_esc(w.label)}  {mark}")
    return "\n".join(rows)


def render_godview(views: list[P.GodView]) -> str:
    """DEBUG 上帝视角 — out-of-world (magenta), DELIBERATELY past the A5 line.
    Per NPC: full stance toward the player, every world-book entry it can access,
    🔒 the ones its faction/region can't, and its private memory."""
    if not views:
        return f"[{MAGENTA}]DEBUG 上帝视角：附近没有 NPC。[/]"
    blocks: list[str] = []
    for v in views:
        lines = [f"[{MAGENTA} bold]☉ {_esc(v.name)}[/]  [dim]（出戏·调试）[/]"]
        if v.relationship:
            dims = "  ".join(f"{_esc(d.label)}{d.value:.2f}" for d in v.relationship)
            lines.append(f"[{MAGENTA}]  立场 {dims}[/]")
        for k in v.knowledge:
            if k.locked:
                lines.append(f"[dim]  🔒 {_esc(k.content)}（派系不可见）[/]")
            else:
                lines.append(f"[{MAGENTA}]  ·（{_esc(k.framing)}）{_esc(k.content)}[/]")
        for m in v.memories[:5]:
            if m:
                lines.append(f"[{MAGENTA} italic]  ◦ {_esc(m)}[/]")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_status(snapshot: P.WorldSnapshot) -> str:
    """One-line status header markup."""
    p = snapshot.player
    hp = f"{p.hp}/{p.max_hp}" if p and p.max_hp else (str(p.hp) if p else "—")
    stamina = p.stamina if p else "—"
    return (
        f"[{RED}]♥[/] HP {hp}   ⚡ 体力 {stamina}   "
        f"Tick {snapshot.tick}   位置 {snapshot.location.name or snapshot.location.id}   "
        f"节奏 {snapshot.pacing}   [dim]·时段* ·天气*[/]"
    )
