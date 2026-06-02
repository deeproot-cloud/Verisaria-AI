"""ANSI text formatter for rich CLI output.

Zero-dependency: uses ANSI escape codes only.
Automatically disabled when stdout is not a TTY (e.g. piped output).
"""

from __future__ import annotations

import sys


class ANSI:
    """ANSI escape code constants."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"

    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_CYAN = "\033[46m"
    BG_WHITE = "\033[47m"


class OutputFormatter:
    """Format engine output with colors, boxes, and status bars."""

    def __init__(self, use_ansi: bool | None = None) -> None:
        if use_ansi is None:
            use_ansi = sys.stdout.isatty()
        self.use_ansi = use_ansi

    # -- Low-level helpers --

    def _c(self, text: str, code: str) -> str:
        if not self.use_ansi:
            return text
        return f"{code}{text}{ANSI.RESET}"

    def bold(self, text: str) -> str:
        return self._c(text, ANSI.BOLD)

    def dim(self, text: str) -> str:
        return self._c(text, ANSI.DIM)

    def red(self, text: str) -> str:
        return self._c(text, ANSI.RED)

    def green(self, text: str) -> str:
        return self._c(text, ANSI.GREEN)

    def yellow(self, text: str) -> str:
        return self._c(text, ANSI.YELLOW)

    def blue(self, text: str) -> str:
        return self._c(text, ANSI.BLUE)

    def cyan(self, text: str) -> str:
        return self._c(text, ANSI.CYAN)

    def magenta(self, text: str) -> str:
        return self._c(text, ANSI.MAGENTA)

    # -- High-level components --

    def header(self, title: str) -> str:
        line = "═" * (len(title) + 4)
        return f"\n{self.bold(self.cyan(line))}\n  {self.bold(title)}  \n{self.bold(self.cyan(line))}"

    def status_bar(
        self,
        hp: int,
        max_hp: int,
        location: str,
        zone: str | None,
        tick: int,
        in_combat: bool = False,
        width: int = 60,
    ) -> str:
        """Render a compact status bar line.

        ``width`` controls the border rule length so the bar adapts to narrow
        terminals (C-4). In combat the borders turn red (C-1).
        """
        loc = f"{location}"
        if zone:
            loc += f" / {zone}"

        hp_color = ANSI.GREEN if hp > max_hp * 0.5 else ANSI.YELLOW if hp > max_hp * 0.25 else ANSI.RED
        hp_str = self._c(f"HP {hp}/{max_hp}", hp_color + ANSI.BOLD)

        tick_str = self._c(f"Tick {tick}", ANSI.DIM)
        loc_str = self._c(loc, ANSI.CYAN)

        parts = [hp_str, loc_str, tick_str]
        if in_combat:
            parts.insert(1, self._c("⚔ COMBAT", ANSI.BG_RED + ANSI.BOLD + ANSI.WHITE))

        bar = " │ ".join(parts)
        rule = "─" * max(1, width)
        border_color = ANSI.RED if in_combat else ANSI.DIM
        border = self._c(rule, border_color)
        return f"{border}\n{bar}\n{border}"

    def box(self, title: str, lines: list[str]) -> str:
        """Draw a simple box around content."""
        width = max(len(title), max((len(line) for line in lines), default=0)) + 4
        top = f"┌{'─' * (width - 2)}┐"
        bottom = f"└{'─' * (width - 2)}┘"
        title_line = f"│ {self.bold(title)}{' ' * (width - len(title) - 3)}│"

        content_lines = []
        for line in lines:
            pad = width - len(line) - 3
            content_lines.append(f"│ {line}{' ' * pad}│")

        return "\n".join([top, title_line] + content_lines + [bottom])

    def option_list(self, options: list[str], start_index: int = 1) -> str:
        """Render numbered options with highlight."""
        lines = []
        for i, opt in enumerate(options, start_index):
            num = self._c(f"[{i}]", ANSI.BOLD + ANSI.YELLOW)
            lines.append(f"  {num} {opt}")
        return "\n".join(lines)

    # -- Content-aware formatters --

    def format_narrative(self, text: str) -> str:
        """Format narrative/gameplay output with semantic colors."""
        if not text:
            return text

        # Preview mode
        if text.startswith("[Preview]"):
            return self.yellow(text)

        # Error / cannot do
        if text.startswith("You cannot do that") or text.startswith("[Error]"):
            return self.red(text)

        # System cancellation / failure
        if "已取消" in text or "多次澄清后仍无法理解" in text:
            return self.yellow(text)

        # Clarification request with options
        if "\n  1. " in text or "输入选项编号" in text:
            return self._format_clarification(text)

        # Success / saved
        if text.startswith("Saved:"):
            return self.green(text)

        # Default narrative
        return text

    def _format_clarification(self, text: str) -> str:
        lines = text.split("\n")
        out: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("1.", "2.", "3.", "4.", "5.")):
                # Option line: highlight number
                num, _, rest = stripped.partition(".")
                out.append(f"  {self._c(f'[{num}]', ANSI.BOLD + ANSI.YELLOW)} {rest.strip()}")
            elif "输入选项编号" in stripped or "请补充说明" in stripped:
                out.append(self.dim(line))
            else:
                out.append(self.yellow(line))
        return "\n".join(out)

    def format_command_output(self, text: str, cmd: str = "") -> str:
        """Format slash-command output."""
        if cmd in ("look", "l"):
            return self._format_look(text)
        if cmd in ("map", "m"):
            return self._format_map(text)
        if cmd in ("who", "w"):
            return self._format_who(text)
        if cmd == "status":
            return self._format_status(text)
        if cmd == "agenda":
            return self._format_agenda(text)
        if cmd in ("help", "h"):
            return self._format_help(text)
        if cmd == "history":
            return self._format_history(text)
        if cmd == "inspect":
            return self._format_inspect(text)
        if cmd == "belief":
            return self._format_beliefs(text)
        if cmd == "memory":
            return self._format_memories(text)
        if text.startswith("Saved:"):
            return self.green(text)
        if text.startswith("Loaded"):
            return self.green(text)
        return text

    def _format_look(self, text: str) -> str:
        lines = text.split("\n")
        out: list[str] = []
        for line in lines:
            if line.startswith("Location:"):
                key, _, val = line.partition(":")
                out.append(f"{self.bold(key)}: {self.cyan(val.strip())}")
            elif line.startswith("Zone:"):
                key, _, val = line.partition(":")
                out.append(f"{self.bold(key)}: {self.cyan(val.strip())}")
            elif line == "You see:":
                out.append(self.bold(line))
            elif line.startswith("  - "):
                out.append(f"  {self._c('•', ANSI.YELLOW)} {line[4:]}")
            elif line == "You are alone.":
                out.append(self.dim(line))
            else:
                out.append(line)
        return "\n".join(out)

    def _format_status(self, text: str) -> str:
        lines = text.split("\n")
        out: list[str] = []
        for line in lines:
            if line.startswith("HP:"):
                key, _, val = line.partition(":")
                hp_part = val.strip()
                if "/" in hp_part:
                    hp_str, _, max_str = hp_part.partition("/")
                    try:
                        hp_val = int(hp_str)
                        max_val = int(max_str)
                        hp_color = ANSI.GREEN if hp_val > max_val * 0.5 else ANSI.YELLOW if hp_val > max_val * 0.25 else ANSI.RED
                        colored_hp = self._c(hp_part, hp_color + ANSI.BOLD)
                        out.append(f"{self.bold(key)}: {colored_hp}")
                        continue
                    except ValueError:
                        pass
                out.append(f"{self.bold(key)}: {hp_part}")
            elif line.startswith("Status: IN COMBAT"):
                out.append(self._c(line, ANSI.BOLD + ANSI.RED))
            elif ":" in line:
                key, _, val = line.partition(":")
                out.append(f"{self.bold(key)}: {val.strip()}")
            else:
                out.append(line)
        return "\n".join(out)

    def _format_agenda(self, text: str) -> str:
        lines = text.split("\n")
        out: list[str] = []
        for line in lines:
            if line.startswith("==="):
                out.append(self.bold(self.cyan(line)))
            elif line.startswith("[") and line.endswith("]"):
                out.append(self.bold(self.yellow(line)))
            elif line.startswith("  - "):
                out.append(f"  {self._c('•', ANSI.GREEN)} {line[4:]}")
            else:
                out.append(line)
        return "\n".join(out)

    def _format_help(self, text: str) -> str:
        lines = text.split("\n")
        out: list[str] = []
        for line in lines:
            if line.startswith("Commands:"):
                out.append(self.bold(line))
            elif line.startswith("  /"):
                cmd_part, _, desc = line[2:].partition("  ")
                out.append(f"  {self._c('/' + cmd_part, ANSI.CYAN)}  {desc.strip()}")
            else:
                out.append(line)
        return "\n".join(out)

    def _format_map(self, text: str) -> str:
        lines = text.split("\n")
        out: list[str] = []
        for line in lines:
            if line.startswith("==="):
                out.append(self.bold(self.cyan(line)))
            elif line.startswith("You are in:"):
                key, _, val = line.partition(":")
                out.append(f"{self.bold(key)}: {self.yellow(val.strip())}")
            elif line == "[Zones]":
                out.append(self.bold(self.yellow(line)))
            elif line == "[Here]":
                out.append(self.bold(self.yellow(line)))
            elif line == "[Exits]":
                out.append(self.bold(self.green(line)))
            elif line == "[Other Locations]":
                out.append(self.bold(self.green(line)))
            elif line.startswith("  → "):
                out.append(f"  {self._c('→', ANSI.GREEN)} {line[4:]}")
            elif "★ You" in line:
                out.append(self._c(line, ANSI.BOLD + ANSI.CYAN))
            elif "★" in line:
                out.append(self._c(line, ANSI.YELLOW))
            else:
                out.append(line)
        return "\n".join(out)

    def _format_who(self, text: str) -> str:
        lines = text.split("\n")
        out: list[str] = []
        for line in lines:
            if line.startswith("==="):
                out.append(self.bold(self.cyan(line)))
            elif line == "You are alone.":
                out.append(self.dim(line))
            elif line.startswith("  "):
                parts = line.split(" — ")
                if len(parts) == 2:
                    name_part = parts[0].strip()
                    info_part = parts[1].strip()
                    # Highlight combat/talking status
                    if "[in combat]" in info_part:
                        info_part = info_part.replace("[in combat]", self._c("[in combat]", ANSI.RED))
                    if "[talking]" in info_part:
                        info_part = info_part.replace("[talking]", self._c("[talking]", ANSI.YELLOW))
                    out.append(f"  {self._c('•', ANSI.GREEN)} {name_part} — {info_part}")
                else:
                    out.append(line)
            else:
                out.append(line)
        return "\n".join(out)

    # -- Debug formatters --

    def _format_history(self, text: str) -> str:
        lines = text.split("\n")
        out: list[str] = []
        for line in lines:
            if line.startswith("==="):
                out.append(self.bold(self.cyan(line)))
            elif line.startswith("  Tick"):
                parts = line.split("│")
                if len(parts) >= 4:
                    tick_part = self._c(parts[0].strip(), ANSI.DIM)
                    type_part = self._c(parts[1].strip(), ANSI.YELLOW)
                    actor_part = self._c(parts[2].strip(), ANSI.CYAN)
                    desc_part = parts[3].strip()
                    out.append(f"  {tick_part} │ {type_part} │ {actor_part} │ {desc_part}")
                else:
                    out.append(line)
            else:
                out.append(line)
        return "\n".join(out)

    def _format_inspect(self, text: str) -> str:
        lines = text.split("\n")
        out: list[str] = []
        for line in lines:
            if line.startswith("==="):
                out.append(self.bold(self.cyan(line)))
            elif ":" in line:
                key, _, val = line.partition(":")
                out.append(f"{self.bold(key)}: {val.strip()}")
            else:
                out.append(line)
        return "\n".join(out)

    def _format_beliefs(self, text: str) -> str:
        lines = text.split("\n")
        out: list[str] = []
        for line in lines:
            if line.startswith("==="):
                out.append(self.bold(self.cyan(line)))
            elif line.startswith("  ["):
                # Confidence bar
                bar_start = line.find("[")
                bar_end = line.find("]")
                if bar_start != -1 and bar_end != -1:
                    bar = line[bar_start : bar_end + 1]
                    rest = line[bar_end + 1 :]
                    out.append(f"  {self._c(bar, ANSI.GREEN)} {rest.strip()}")
                else:
                    out.append(line)
            else:
                out.append(line)
        return "\n".join(out)

    def _format_memories(self, text: str) -> str:
        lines = text.split("\n")
        out: list[str] = []
        for line in lines:
            if line.startswith("==="):
                out.append(self.bold(self.cyan(line)))
            elif line.startswith("[") and line.endswith("]"):
                out.append(self.bold(self.yellow(line)))
            elif line.startswith("  Tick"):
                parts = line.split("│")
                if len(parts) >= 3:
                    tick_part = self._c(parts[0].strip(), ANSI.DIM)
                    sal_part = self._c(parts[1].strip(), ANSI.YELLOW)
                    content_part = parts[2].strip()
                    out.append(f"  {tick_part} │ {sal_part} │ {content_part}")
                else:
                    out.append(line)
            else:
                out.append(line)
        return "\n".join(out)
