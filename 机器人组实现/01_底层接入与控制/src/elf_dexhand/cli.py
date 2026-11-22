"""Command-line entry point for checking, previewing, and moving the hand."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .connection import BoardConfig, BoardConnection, BoardConnectionError
from .controller import HandController
from .poses import PoseLibrary
from .protocol import Joint, encode_cansend


def _parser() -> argparse.ArgumentParser:
    """Construct the command parser; no connection is made."""
    parser = argparse.ArgumentParser(prog="dexhand")
    parser.add_argument("--host", default=os.environ.get("BOARD_HOST"))
    parser.add_argument("--user", default=os.environ.get("BOARD_USER", "root"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("BOARD_PORT", "22"))
    )
    parser.add_argument("--key", type=Path, default=os.environ.get("BOARD_KEY"))
    parser.add_argument("--interface", default="can0")
    parser.add_argument("--trust-on-first-use", action="store_true")
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("check", help="Check SSH, cansend, and the CAN interface")

    frame = commands.add_parser("frame", help="Preview one frame without sending it")
    frame.add_argument("joint")
    frame.add_argument("position", type=int)

    joint = commands.add_parser("joint", help="Move one joint once")
    joint.add_argument("joint")
    joint.add_argument("position", type=int)

    poses = commands.add_parser("poses", help="List available YAML poses")
    poses.add_argument("--file", type=Path)

    pose = commands.add_parser("pose", help="Send a named YAML pose once")
    pose.add_argument("name")
    pose.add_argument("--file", type=Path)
    pose.add_argument("--preview", action="store_true", help="Print frames only")
    return parser


def _library(path: Path | None) -> PoseLibrary:
    """Load a user-provided pose file or the packaged examples."""
    return PoseLibrary.from_file(path) if path is not None else PoseLibrary.default()


def main(argv: list[str] | None = None) -> int:
    """Run one CLI action; print failures and return 1 on operational errors."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.action == "frame":
            print(encode_cansend(args.joint, args.position, args.interface))
            return 0
        if args.action == "poses":
            print("\n".join(_library(args.file).poses))
            return 0
        library = _library(args.file) if args.action == "pose" else None
        if args.action == "pose" and args.preview:
            assert library is not None
            positions = library.get(args.name)
            for joint in Joint:
                print(encode_cansend(joint, positions[joint], args.interface))
            return 0
        if not args.host:
            parser.error("--host or BOARD_HOST is required for board access")
        board = BoardConnection(
            BoardConfig(
                host=args.host,
                user=args.user,
                port=args.port,
                key_path=Path(args.key) if args.key else None,
                trust_on_first_use=args.trust_on_first_use,
            )
        )
        hand = HandController(board, interface=args.interface)
        if args.action == "check":
            print(f"Connected to {hand.check_ready()}; {args.interface} is up")
        elif args.action == "joint":
            hand.set_joint(args.joint, args.position)
            print(f"Sent {encode_cansend(args.joint, args.position, args.interface)}")
        elif args.action == "pose":
            assert library is not None
            hand.perform(args.name, library)
            print(f"Sent pose {args.name!r}")
        return 0
    except (OSError, ValueError, KeyError, BoardConnectionError, RuntimeError) as exc:
        print(f"dexhand: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
