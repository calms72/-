"""Verify paced CAN streaming and time-based interpolation without hardware."""

from __future__ import annotations

import time
import unittest

from elf_dexhand.connection import CommandResult
from elf_dexhand.protocol import Joint
from elf_dexhand.streaming import (
    SAFE_POSE,
    RealtimeCanStream,
    SpeedLimitedTrajectory,
    candump_batch,
)


class FakeChannel:
    """Collect stdin batches from the stream worker in memory."""

    def __init__(self) -> None:
        """Start with an open, silent remote process."""
        self.batches: list[bytes] = []
        self.input_closed = False

    def settimeout(self, _seconds: float) -> None:
        """Accept a channel timeout without changing fake behavior."""

    def sendall(self, data: bytes) -> None:
        """Record one six-frame batch or reject writes after EOF."""
        if self.input_closed:
            raise RuntimeError("fake channel was already closed")
        self.batches.append(data)

    def recv_stderr_ready(self) -> bool:
        """Report that the fake remote process has no diagnostics."""
        return False

    def exit_status_ready(self) -> bool:
        """Report completion after the caller closes stdin."""
        return self.input_closed

    def shutdown_write(self) -> None:
        """Simulate EOF on the remote process's stdin."""
        self.input_closed = True

    def recv_exit_status(self) -> int:
        """Report a successful fake process exit."""
        return 0

    def close(self) -> None:
        """Mark the fake channel closed."""
        self.input_closed = True


class FakeBoard:
    """Provide the commands needed to start one fake canplayer."""

    def __init__(self) -> None:
        """Own one in-memory channel."""
        self.channel = FakeChannel()

    def run(self, command: str) -> CommandResult:
        """Treat dependency checks as successful and no player as active."""
        return CommandResult(command, 0, "", "")

    def open_channel(self, _command: str) -> FakeChannel:
        """Return the recording channel without opening SSH."""
        return self.channel


class StreamingTests(unittest.TestCase):
    """Check frame encoding, speed limits, and final safe pose."""

    def test_trajectory_uses_elapsed_time(self) -> None:
        """Large camera jumps are interpolated at the configured speed."""
        trajectory = SpeedLimitedTrajectory(SAFE_POSE, max_speed=6000)
        target = dict(SAFE_POSE)
        target[Joint.INDEX] = 3200
        self.assertEqual(trajectory.advance(target, 0.02)[Joint.INDEX], 120)
        self.assertEqual(trajectory.advance(target, 0.02)[Joint.INDEX], 240)
        self.assertEqual(trajectory.advance(target, 0.5)[Joint.INDEX], 3200)

    def test_candump_batch_contains_six_ordered_frames(self) -> None:
        """One replay batch contains six valid IDs and spaced timestamps."""
        lines = candump_batch(SAFE_POSE, 1_750_000_000_000_000).decode().splitlines()
        self.assertEqual(len(lines), 6)
        self.assertIn("can0 001#AA0000E809640300", lines[0])
        self.assertIn("can0 002#AA6702E809640300", lines[1])
        self.assertTrue(lines[-1].startswith("(1750000000.002500)"))

    def test_stream_coalesces_targets_and_ends_safe(self) -> None:
        """The writer runs independently and closes with a safe-pose batch."""
        board = FakeBoard()
        stream = RealtimeCanStream(board, rate_hz=50, max_speed=6000)
        stream.start()
        target = dict(SAFE_POSE)
        target[Joint.INDEX] = 3200
        stream.update_target(target)
        time.sleep(0.09)
        stream.close()
        self.assertGreaterEqual(len(board.channel.batches), 3)
        self.assertTrue(
            any(b"003#AA0000E809640300" not in batch for batch in board.channel.batches)
        )
        self.assertEqual(board.channel.batches[-1].count(b"\n"), 6)
        self.assertIn(b"003#AA0000E809640300", board.channel.batches[-1])


if __name__ == "__main__":
    unittest.main()
