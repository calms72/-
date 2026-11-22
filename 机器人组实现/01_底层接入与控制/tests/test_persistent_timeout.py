"""Verify SSH connection failures respect the configured TCP timeout."""

from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from elf_dexhand.connection import BoardConfig, BoardConnectionError
from elf_dexhand.persistent import PersistentBoardConnection


class PersistentTimeoutTests(unittest.TestCase):
    """Ensure an unreachable board cannot hold the arming path indefinitely."""

    def test_tcp_connect_uses_timeout_and_reports_failure(self) -> None:
        """A socket timeout becomes a visible board connection error."""
        board = PersistentBoardConnection(
            BoardConfig(host="2001:db8::10", connect_timeout=3)
        )
        with patch(
            "elf_dexhand.persistent.socket.create_connection",
            side_effect=socket.timeout("timed out"),
        ) as connect:
            with self.assertRaisesRegex(BoardConnectionError, "timed out"):
                board.connect()
        connect.assert_called_once_with(("2001:db8::10", 22), timeout=3)


if __name__ == "__main__":
    unittest.main()
