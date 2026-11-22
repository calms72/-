"""Check that failed physical arming leaves the board connection closed."""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import MagicMock, patch

from elf_dexhand.camera_app import _start_control
from elf_dexhand.connection import BoardConnectionError


class CameraStartTests(unittest.TestCase):
    """Cover the board-offline path without a camera or physical CAN output."""

    def test_offline_board_closes_connection(self) -> None:
        """An SSH error on arming is retryable and releases its resources."""
        args = argparse.Namespace(
            board_host="2001:db8::10",
            board_user="root",
            board_key=None,
            stream_hz=50.0,
            max_speed=6000.0,
            lost_timeout=0.7,
        )
        board = MagicMock()
        with (
            patch("elf_dexhand.camera_app.PersistentBoardConnection") as connection,
            patch("elf_dexhand.camera_app.HandController") as controller,
        ):
            connection.return_value = board
            controller.return_value.check_ready.side_effect = BoardConnectionError(
                "board unreachable"
            )
            with self.assertRaisesRegex(BoardConnectionError, "board unreachable"):
                _start_control(args)
        board.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
