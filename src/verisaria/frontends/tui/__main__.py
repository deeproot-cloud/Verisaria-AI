"""Entry point: ``python -m verisaria.frontends.tui <pack> [--llm ...] [--save-dir ...]``."""
from __future__ import annotations

import argparse
import logging

from verisaria.protocol.engine_session import EngineSession
from verisaria.frontends.tui.app import VerisariaApp


def _configure_log(path: str) -> None:
    """Trace every command / event / tick timing / error to ``path`` (so a session's
    problems are reproducible after the fact). Off unless --log is given."""
    handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("verisaria")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.info("=== Verisaria TUI run log ===")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verisaria-tui", description="Verisaria TUI")
    parser.add_argument("content_pack", help="Path to content pack JSON")
    parser.add_argument(
        "--llm", default="fake",
        choices=["fake", "ollama", "openai", "minimax"], help="LLM backend",
    )
    parser.add_argument("--save-dir", default="saves", help="Save directory")
    parser.add_argument(
        "--log", nargs="?", const="verisaria-tui.log", default=None,
        metavar="FILE", help="Write a run log (default file: verisaria-tui.log)",
    )
    args = parser.parse_args(argv)

    if args.log:
        _configure_log(args.log)

    engine = EngineSession.start(
        args.content_pack, save_dir=args.save_dir, llm_backend=args.llm
    )
    VerisariaApp(engine).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
