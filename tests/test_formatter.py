"""Tests for OutputFormatter ANSI formatting utilities."""

from __future__ import annotations

import pytest

from verisaria.engine.formatter import ANSI, OutputFormatter


class TestANSIBasic:
    def test_color_when_ansi_enabled(self):
        fmt = OutputFormatter(use_ansi=True)
        result = fmt.red("hello")
        assert ANSI.RED in result
        assert ANSI.RESET in result
        assert "hello" in result

    def test_color_when_ansi_disabled(self):
        fmt = OutputFormatter(use_ansi=False)
        result = fmt.red("hello")
        assert result == "hello"

    def test_bold_and_dim(self):
        fmt = OutputFormatter(use_ansi=True)
        assert ANSI.BOLD in fmt.bold("x")
        assert ANSI.DIM in fmt.dim("x")

    def test_cyan_blue_magenta_yellow_green(self):
        fmt = OutputFormatter(use_ansi=True)
        assert ANSI.CYAN in fmt.cyan("x")
        assert ANSI.BLUE in fmt.blue("x")
        assert ANSI.MAGENTA in fmt.magenta("x")
        assert ANSI.YELLOW in fmt.yellow("x")
        assert ANSI.GREEN in fmt.green("x")


class TestStatusBar:
    def test_status_bar_contains_hp_location_tick(self):
        fmt = OutputFormatter(use_ansi=False)
        bar = fmt.status_bar(hp=80, max_hp=100, location="tavern", zone="main_hall", tick=42)
        assert "HP 80/100" in bar
        assert "tavern / main_hall" in bar
        assert "Tick 42" in bar

    def test_status_bar_no_zone(self):
        fmt = OutputFormatter(use_ansi=False)
        bar = fmt.status_bar(hp=50, max_hp=100, location="street", zone=None, tick=1)
        assert "street" in bar
        assert "/" not in bar.split("street")[1].split("Tick")[0]

    def test_status_bar_combat_indicator(self):
        fmt = OutputFormatter(use_ansi=False)
        bar = fmt.status_bar(hp=100, max_hp=100, location="arena", zone=None, tick=5, in_combat=True)
        assert "COMBAT" in bar

    def test_status_bar_hp_colors(self):
        fmt = OutputFormatter(use_ansi=True)
        # High HP → green
        bar = fmt.status_bar(hp=80, max_hp=100, location="x", zone=None, tick=1)
        assert ANSI.GREEN in bar
        # Medium HP → yellow
        bar = fmt.status_bar(hp=40, max_hp=100, location="x", zone=None, tick=1)
        assert ANSI.YELLOW in bar
        # Low HP → red
        bar = fmt.status_bar(hp=10, max_hp=100, location="x", zone=None, tick=1)
        assert ANSI.RED in bar

    # -- C-1: combat-mode border colour --

    def test_status_bar_border_red_in_combat(self):
        fmt = OutputFormatter(use_ansi=True)
        bar = fmt.status_bar(hp=100, max_hp=100, location="arena", zone=None,
                             tick=5, in_combat=True)
        # The border rule line is rendered in red during combat.
        border_lines = [ln for ln in bar.splitlines() if "─" in ln]
        assert border_lines
        assert all(ANSI.RED in ln for ln in border_lines), "combat borders should be red"

    def test_status_bar_border_not_red_when_calm(self):
        fmt = OutputFormatter(use_ansi=True)
        bar = fmt.status_bar(hp=100, max_hp=100, location="arena", zone=None, tick=5)
        border_lines = [ln for ln in bar.splitlines() if "─" in ln]
        assert border_lines
        assert all(ANSI.RED not in ln for ln in border_lines), "calm borders are dim, not red"

    # -- C-4: responsive width --

    def test_status_bar_respects_width(self):
        fmt = OutputFormatter(use_ansi=False)
        bar = fmt.status_bar(hp=80, max_hp=100, location="x", zone=None, tick=1, width=40)
        border_lines = [ln for ln in bar.splitlines() if set(ln) == {"─"}]
        assert border_lines
        assert all(len(ln) == 40 for ln in border_lines)

    def test_status_bar_default_width_60(self):
        fmt = OutputFormatter(use_ansi=False)
        bar = fmt.status_bar(hp=80, max_hp=100, location="x", zone=None, tick=1)
        border_lines = [ln for ln in bar.splitlines() if set(ln) == {"─"}]
        assert all(len(ln) == 60 for ln in border_lines)

    def test_status_bar_narrow_width_still_has_core_info(self):
        fmt = OutputFormatter(use_ansi=False)
        bar = fmt.status_bar(hp=80, max_hp=100, location="tavern", zone=None,
                             tick=42, width=30)
        assert "HP 80/100" in bar
        assert "Tick 42" in bar


class TestBox:
    def test_box_renders_title_and_lines(self):
        fmt = OutputFormatter(use_ansi=False)
        text = fmt.box("Room", ["A table", "A chair"])
        assert "Room" in text
        assert "A table" in text
        assert "A chair" in text
        assert "┌" in text
        assert "└" in text

    def test_box_empty_lines(self):
        fmt = OutputFormatter(use_ansi=False)
        text = fmt.box("Empty", [])
        assert "Empty" in text
        assert "┌" in text


class TestOptionList:
    def test_option_list_numbered(self):
        fmt = OutputFormatter(use_ansi=False)
        text = fmt.option_list(["A", "B", "C"])
        assert "[1] A" in text
        assert "[2] B" in text
        assert "[3] C" in text

    def test_option_list_custom_start(self):
        fmt = OutputFormatter(use_ansi=False)
        text = fmt.option_list(["X"], start_index=5)
        assert "[5] X" in text


class TestFormatNarrative:
    def test_preview_yellow(self):
        fmt = OutputFormatter(use_ansi=True)
        text = fmt.format_narrative("[Preview] movement: {}")
        assert ANSI.YELLOW in text

    def test_error_red(self):
        fmt = OutputFormatter(use_ansi=True)
        text = fmt.format_narrative("You cannot do that: reason")
        assert ANSI.RED in text

    def test_system_cancel_yellow(self):
        fmt = OutputFormatter(use_ansi=True)
        text = fmt.format_narrative("动作已取消。")
        assert ANSI.YELLOW in text

    def test_max_rounds_yellow(self):
        fmt = OutputFormatter(use_ansi=True)
        text = fmt.format_narrative("多次澄清后仍无法理解，请换一种方式描述。")
        assert ANSI.YELLOW in text

    def test_saved_green(self):
        fmt = OutputFormatter(use_ansi=True)
        text = fmt.format_narrative("Saved: abc123")
        assert ANSI.GREEN in text

    def test_clarification_highlighted(self):
        fmt = OutputFormatter(use_ansi=True)
        text = "问题？\n  1. A\n  2. B\n输入选项编号或补充说明"
        result = fmt.format_narrative(text)
        assert ANSI.YELLOW in result
        assert "[1]" in result or "1." in result

    def test_plain_narrative_unchanged(self):
        fmt = OutputFormatter(use_ansi=False)
        text = "The sun sets over the horizon."
        assert fmt.format_narrative(text) == text


class TestFormatCommandOutput:
    def test_look_formatting(self):
        fmt = OutputFormatter(use_ansi=False)
        raw = "Location: tavern\nZone: main\nYou see:\n  - npc_001\nYou are alone."
        text = fmt.format_command_output(raw, cmd="look")
        assert "tavern" in text
        assert "npc_001" in text

    def test_status_hp_color(self):
        fmt = OutputFormatter(use_ansi=True)
        raw = "HP: 20/100\nLocation: x / y"
        text = fmt.format_command_output(raw, cmd="status")
        assert ANSI.RED in text

    def test_status_combat_red(self):
        fmt = OutputFormatter(use_ansi=True)
        raw = "Status: IN COMBAT"
        text = fmt.format_command_output(raw, cmd="status")
        assert ANSI.RED in text

    def test_agenda_formatting(self):
        fmt = OutputFormatter(use_ansi=True)
        raw = "=== 议程 (Tick 5) ===\n[已确认目标]\n  - 找食物"
        text = fmt.format_command_output(raw, cmd="agenda")
        assert ANSI.CYAN in text
        assert ANSI.YELLOW in text

    def test_help_formatting(self):
        fmt = OutputFormatter(use_ansi=True)
        raw = "Commands:\n  /save         Save\n  /look         Look"
        text = fmt.format_command_output(raw, cmd="help")
        assert ANSI.CYAN in text

    def test_saved_green_cmd(self):
        fmt = OutputFormatter(use_ansi=True)
        text = fmt.format_command_output("Saved: abc", cmd="save")
        assert ANSI.GREEN in text

    def test_loaded_green_cmd(self):
        fmt = OutputFormatter(use_ansi=True)
        text = fmt.format_command_output("Loaded save abc at tick 5", cmd="load")
        assert ANSI.GREEN in text

    def test_unknown_cmd_passes_through(self):
        fmt = OutputFormatter(use_ansi=False)
        text = fmt.format_command_output("some output", cmd="unknown")
        assert text == "some output"


class TestHeader:
    def test_header_contains_title(self):
        fmt = OutputFormatter(use_ansi=False)
        text = fmt.header("Test")
        assert "Test" in text
        assert "═" in text
