"""Tests for command safety, YAML validation, and SSH error reporting."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from elf_dexhand.connection import (
    BoardConfig,
    BoardConnection,
    BoardConnectionError,
    CommandResult,
    RemoteCommandError,
)
from elf_dexhand.controller import HandController
from elf_dexhand.poses import PoseFormatError, PoseLibrary
from elf_dexhand.protocol import Joint, encode_cansend


class RecordingBoard:
    """Record remote commands without connecting to physical hardware."""

    def __init__(self) -> None:
        """Start an empty in-memory record of remote commands."""
        self.commands: list[str] = []

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        """Capture a command and emulate successful remote execution."""
        self.commands.append(command)
        return CommandResult(command, 0, "", "")

    def check_connection(self) -> str:
        """Emulate an already authenticated board."""
        return "test-board"


class ProtocolTests(unittest.TestCase):
    """Check protocol boundaries before CAN frames can reach the board."""

    def test_position_is_little_endian(self) -> None:
        """Position 2000 is sent with low byte D0 before high byte 07."""
        self.assertEqual(encode_cansend("食指", 2000), "003#AAD007E809640300")

    def test_rejects_out_of_range_and_shell_interface(self) -> None:
        """Invalid values and interface names cannot produce a frame."""
        with self.assertRaises(ValueError):
            encode_cansend("拇指侧摆", 2001)
        with self.assertRaises(ValueError):
            encode_cansend("食指", 10, "can0; reboot")
        with self.assertRaises(TypeError):
            encode_cansend("食指", True)

    def test_pose_validation_sends_nothing_on_failure(self) -> None:
        """One invalid joint prevents every frame in a six-joint pose."""
        board = RecordingBoard()
        hand = HandController(board)
        invalid = {joint: 0 for joint in Joint}
        invalid[Joint.THUMB_SPREAD] = 3000
        with self.assertRaises(ValueError):
            hand.set_pose(invalid)
        self.assertEqual(board.commands, [])

    def test_default_yeah_pose_sends_six_frames_in_order(self) -> None:
        """The bundled gesture produces one command for every joint."""
        board = RecordingBoard()
        hand = HandController(board)
        hand.perform("yeah", PoseLibrary.default())
        self.assertEqual(len(board.commands), 1)
        command = board.commands[0]
        self.assertEqual(command.count("cansend can0"), 6)
        self.assertLess(command.index("001#"), command.index("006#"))
        self.assertIn("003#AA0000E809640300", command)
        self.assertIn("005#AAFF0FE809640300", command)


class PoseTests(unittest.TestCase):
    """Reject malformed data before any physical action is requested."""

    def _load_text(self, text: str) -> PoseLibrary:
        """Write one temporary YAML document and parse it."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "poses.yaml"
            path.write_text(text, encoding="utf-8")
            return PoseLibrary.from_file(path)

    def test_missing_joint_is_reported(self) -> None:
        """An incomplete pose is rejected with the missing joint name."""
        with self.assertRaisesRegex(PoseFormatError, "PINKY"):
            self._load_text(
                "poses:\n  incomplete:\n"
                "    thumb_spread: 0\n    thumb_bend: 0\n"
                "    index: 0\n    middle: 0\n    ring: 0\n"
            )

    def test_duplicate_yaml_key_is_reported(self) -> None:
        """A repeated field never silently replaces an earlier value."""
        with self.assertRaisesRegex(PoseFormatError, "duplicate YAML key"):
            self._load_text("poses:\n  yeah:\n    index: 0\n    index: 4095\n")


class ConnectionTests(unittest.TestCase):
    """Check SSH command invocation and distinct error categories."""

    @patch("elf_dexhand.connection.shutil.which", return_value="ssh")
    @patch("elf_dexhand.connection.subprocess.run")
    def test_connection_check_logs_in(self, run: object, _which: object) -> None:
        """Checking a connection executes hostname and returns its value."""
        run.return_value = SimpleNamespace(  # type: ignore[attr-defined]
            returncode=0, stdout="example-board\n", stderr=""
        )
        board = BoardConnection(BoardConfig(host="192.0.2.10"))
        self.assertEqual(board.check_connection(), "example-board")
        argv = run.call_args.args[0]  # type: ignore[attr-defined]
        self.assertIn("BatchMode=yes", argv)
        self.assertEqual(argv[-1], "hostname")

    @patch("elf_dexhand.connection.shutil.which", return_value="ssh")
    @patch("elf_dexhand.connection.subprocess.run")
    def test_failed_ssh_is_not_suppressed(self, run: object, _which: object) -> None:
        """An authentication failure raises a connection-specific error."""
        run.return_value = SimpleNamespace(  # type: ignore[attr-defined]
            returncode=255, stdout="", stderr="Permission denied"
        )
        board = BoardConnection(BoardConfig(host="192.0.2.10"))
        with self.assertRaisesRegex(BoardConnectionError, "Permission denied"):
            board.check_connection()

    @patch("elf_dexhand.connection.shutil.which", return_value="ssh")
    @patch("elf_dexhand.connection.subprocess.run")
    def test_remote_failure_and_timeout_are_reported(
        self, run: object, _which: object
    ) -> None:
        """A remote exit code and a process timeout raise useful exceptions."""
        board = BoardConnection(BoardConfig(host="192.0.2.10"))
        run.return_value = SimpleNamespace(  # type: ignore[attr-defined]
            returncode=127, stdout="", stderr="cansend: not found"
        )
        with self.assertRaisesRegex(RemoteCommandError, "cansend: not found"):
            board.run("cansend can0 003#AA0000E809640300")
        run.side_effect = subprocess.TimeoutExpired("ssh", 1)  # type: ignore[attr-defined]
        with self.assertRaises(BoardConnectionError):
            board.run("hostname", timeout=1)


if __name__ == "__main__":
    unittest.main()
