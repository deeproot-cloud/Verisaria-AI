"""Pure renderer tests (no Textual) — protocol Event → Rich markup, color legend."""
from __future__ import annotations

from verisaria.frontends.tui import render as R
from verisaria import protocol as P


def test_player_and_npc_lines_colored_and_tick_tagged():
    ps = R.render_event(P.PlayerSpoke(tick=9, line="你好"))
    assert "你：你好" in ps and R.AMBER in ps and "\\[9]" in ps
    ns = R.render_event(P.NpcSpoke(tick=9, npc_id="npc.captain_brann", name="captain_brann", line="哼。"))
    assert "captain_brann：哼。" in ns and R.PARCHMENT in ns


def test_pressure_red_world_change_green():
    pr = R.render_event(P.PressureEvent(tick=3, event_type="x", source="y", summary="难民被拒之门外"))
    assert "(压力)" in pr and R.RED in pr
    on = R.render_event(P.WorldVarChanged(tick=5, var_id="refugees_admitted", label="难民入营", value=True))
    assert "✓" in on and R.GREEN in on
    off = R.render_event(P.WorldVarChanged(tick=5, var_id="refugees_admitted", label="难民入营", value=False))
    assert "✗" in off


def test_control_events_not_logged():
    assert R.render_event(P.TickAdvanced(tick=9, new_tick=9)) is None
    assert R.render_event(P.SpeechToken(tick=9, npc_id="x", token="a")) is None


def test_narration_rendered_as_prose():
    out = R.render_event(P.Narration(tick=9, text="你环顾四周。"))
    assert out is not None and "你环顾四周。" in out and "italic" in out


def test_markup_in_content_is_escaped():
    out = R.render_event(P.NpcSpoke(tick=1, npc_id="n", name="n", line="危险[red]注入[/]"))
    assert "\\[red]" in out  # the content's bracket is escaped, not interpreted


def test_status_line_has_core_fields():
    snap = P.WorldSnapshot(
        tick=7, pacing="normal",
        location=P.LocationView(id="gatehouse"),
        player=P.PlayerView(hp=100, max_hp=100, stamina=80),
    )
    s = R.render_status(snap)
    assert "100/100" in s and "Tick 7" in s and "gatehouse" in s and "体力 80" in s


def test_render_nearby_shows_dominant_dim_and_phrase():
    snap = P.WorldSnapshot(
        tick=5, pacing="normal", location=P.LocationView(id="gatehouse"),
        present=[
            P.PresentEntity(id="npc.captain_brann", name="captain_brann", type="npc"),
            P.PresentEntity(id="player_001", name="一个外来者", type="player"),
        ],
        relationships=[
            P.RelationshipView(
                npc_id="npc.captain_brann", name="captain_brann",
                descriptors=[P.relationship_descriptor("suspicion", 0.51)],
            )
        ],
    )
    out = R.render_nearby(snap)
    assert "captain_brann" in out
    assert "怀疑" in out and "戒心很重" in out and "█" in out
    assert "一个外来者" not in out  # players aren't listed as nearby NPCs


def test_render_nearby_empty():
    snap = P.WorldSnapshot(tick=1, pacing="normal", location=P.LocationView(id="x"))
    assert "此处无人" in R.render_nearby(snap)


def test_render_world_marks_flags():
    snap = P.WorldSnapshot(
        tick=1, pacing="normal", location=P.LocationView(id="x"),
        world_vars=[
            P.WorldVarView(var_id="refugees_admitted", label="难民入营", value=False),
            P.WorldVarView(var_id="gate_open", label="城门开启", value=True),
        ],
    )
    out = R.render_world(snap)
    assert "难民入营" in out and "✗" in out
    assert "城门开启" in out and "✓" in out


def test_summarize_event_plain_text():
    assert "你好" in R.summarize_event(P.PlayerSpoke(tick=1, line="你好"))
    s = R.summarize_event(P.NpcSpoke(tick=1, npc_id="npc.brann", name="brann", line="哼"))
    assert "brann" in s and "哼" in s and "[" not in s  # plain, no markup
    rs = R.summarize_event(P.RelationshipShifted(
        tick=1, npc_id="n", name="brann",
        descriptor=P.relationship_descriptor("suspicion", 0.5), delta=0.2))
    assert "brann" in rs and "+0.20" in rs


def test_notice_rendered_dim():
    out = R.render_event(P.Notice(tick=4, text="队长不在这里"))
    assert out is not None and "队长不在这里" in out and "dim" in out


def test_render_map_shows_current_and_exits():
    snap = P.WorldSnapshot(
        tick=1, pacing="normal", location=P.LocationView(id="gatehouse", name="门楼"),
        map=P.MapView(current="gatehouse", current_name="门楼", exits=[
            P.MapExit(to="barracks", name="兵营", distance="adjacent"),
            P.MapExit(to="refugee_camp", name="难民营", distance="near"),
        ], others=["集市"]),
    )
    out = R.render_map(snap)
    assert "★ 门楼" in out and "兵营" in out and "相邻" in out
    assert "难民营" in out and "附近" in out and "集市" in out


def test_render_agenda_shows_stances_drives_questions():
    snap = P.WorldSnapshot(
        tick=1, pacing="normal", location=P.LocationView(id="x"),
        agenda=P.AgendaView(
            confirmed_stances=["接纳难民"], drives=["弄清这里发生了什么"],
            open_questions=["队长会为谁担风险？"],
        ),
    )
    out = R.render_agenda(snap)
    assert "接纳难民" in out and "弄清这里发生了什么" in out and "队长会为谁担风险" in out


def test_bar_is_solid_and_scales():
    assert R._bar(1.0, 10) == "█" * 10
    assert R._bar(0.0, 10) == "░" * 10
    assert "█" in R._bar(0.5, 10) and "░" in R._bar(0.5, 10)
