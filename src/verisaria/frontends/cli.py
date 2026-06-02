"""CLI/REPL frontend: argparse entry points that drive a GameSession.

Commands:
    run     <content_pack>      Start a new game session
    load    <save_id>           Load an existing save
    replay  <save_id>           Replay a save for verification
    validate <content_pack>     Validate a content pack
"""
from __future__ import annotations

import argparse
import sys

from verisaria.runtime.session import GameSession
from verisaria.engine.persistence import PersistenceLayer
from verisaria.engine.campaign_loader import CampaignLoader


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    """Start a new game session."""
    session = GameSession(args.content_pack, save_dir=args.save_dir, llm_backend=args.llm)
    session.repl()
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    """Load a saved game."""
    # We need a content pack to bootstrap; try to infer from save
    persistence = PersistenceLayer(args.save_dir)
    save_data = persistence.load(args.save_id)

    # Find content pack path
    pack_path = args.content_pack or f"fixtures/content_packs/{save_data.content_pack_id}.json"
    session = GameSession(pack_path, save_dir=args.save_dir, llm_backend=args.llm)
    session.world = persistence.restore_world_core(save_data)
    print(f"Loaded save {args.save_id} at tick {session.world.state.tick}")
    session.repl()
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate a content pack."""
    try:
        pack = CampaignLoader.load_from_file(args.content_pack)
    except Exception as exc:
        print(f"Failed to load: {exc}")
        return 1

    result = CampaignLoader.validate(pack)
    if result.valid:
        print("Content pack is valid.")
        return 0

    print("Validation failed:")
    for issue in result.issues:
        print(f"  [{issue.severity}] {issue.rule}: {issue.message}")
    return 1


def cmd_replay(args: argparse.Namespace) -> int:
    """Replay a save for verification."""
    persistence = PersistenceLayer(args.save_dir)
    try:
        save_data = persistence.load(args.save_id)
    except FileNotFoundError:
        print(f"Save not found: {args.save_id}")
        return 1

    is_valid, issues = persistence.verify(save_data)
    if is_valid:
        print(f"Save {args.save_id} passed verification.")
    else:
        print(f"Save {args.save_id} verification failed:")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    # Restore and print summary
    world = persistence.restore_world_core(save_data)
    print(f"Restored world at tick {world.state.tick}")
    print(f"Entities: {len(world.state.entities)}")
    print(f"Events: {len(world.event_log)}")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verisaria",
        description="LLM-driven RPG world engine CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run
    run_parser = subparsers.add_parser("run", help="Start a new game session")
    run_parser.add_argument("content_pack", help="Path to content pack JSON or directory")
    run_parser.add_argument("--save-dir", default="saves", help="Save directory")
    run_parser.add_argument("--llm", default="fake", choices=["fake", "ollama", "openai", "minimax"], help="LLM backend")

    # load
    load_parser = subparsers.add_parser("load", help="Load a saved game")
    load_parser.add_argument("save_id", help="Save ID to load")
    load_parser.add_argument("--content-pack", help="Content pack path (optional)")
    load_parser.add_argument("--save-dir", default="saves", help="Save directory")
    load_parser.add_argument("--llm", default="fake", choices=["fake", "ollama", "openai", "minimax"], help="LLM backend")

    # validate
    validate_parser = subparsers.add_parser("validate", help="Validate a content pack")
    validate_parser.add_argument("content_pack", help="Path to content pack JSON or directory")

    # replay
    replay_parser = subparsers.add_parser("replay", help="Replay and verify a save")
    replay_parser.add_argument("save_id", help="Save ID to replay")
    replay_parser.add_argument("--save-dir", default="saves", help="Save directory")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "run": cmd_run,
        "load": cmd_load,
        "validate": cmd_validate,
        "replay": cmd_replay,
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
