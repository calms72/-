"""Translate named joints and poses into remote ``cansend`` commands."""

from __future__ import annotations

import shlex
from typing import Mapping

from .connection import BoardClient, CommandResult
from .poses import PoseLibrary
from .protocol import Joint, encode_cansend, parse_joint, validate_interface


class HandController:
    """Send validated single-joint or complete-pose commands to the board.

    Each method sends one set of frames. The hand may reset at a later device
    cycle; this class does not run a background refresh process.
    """

    def __init__(self, board: BoardClient, interface: str = "can0") -> None:
        """Bind a board and CAN interface without transmitting anything."""
        self.board = board
        self.interface = validate_interface(interface)

    def check_ready(self) -> str:
        """Check SSH, ``cansend``, and CAN link state without moving the hand.

        Returns the board hostname. Raises on any unavailable dependency or
        down interface; the only remote side effect is reading system state.
        """
        hostname = self.board.check_connection()
        self.board.run("command -v cansend >/dev/null")
        result = self.board.run(f"ip -brief link show {shlex.quote(self.interface)}")
        fields = result.stdout.split()
        if len(fields) < 2 or fields[1] != "UP":
            raise RuntimeError(
                f"CAN interface {self.interface} is not up: {result.stdout.strip()}"
            )
        return hostname

    def set_joint(self, joint: Joint | int | str, position: int) -> CommandResult:
        """Move one named joint to a raw position and return command output.

        Raises for unknown joints, out-of-range positions, SSH failures, or
        ``cansend`` failures. The position is sent once; it is not held.
        """
        frame = encode_cansend(joint, position, self.interface)
        command = f"cansend {shlex.quote(self.interface)} {shlex.quote(frame)}"
        return self.board.run(command)

    def set_finger(self, finger: Joint | int | str, position: int) -> CommandResult:
        """Alias set_joint for fingers with one motor, such as '食指'.

        The thumb has two motors; select '拇指侧摆' or '拇指弯曲'.
        Errors and physical side effects are identical to set_joint.
        """
        return self.set_joint(finger, position)

    def set_pose(self, positions: Mapping[Joint | int | str, int]) -> CommandResult:
        """Validate six positions, then send six frames in CAN ID order.

        No frame is sent when local validation fails. Once transmission begins,
        earlier joints may have moved if a later remote ``cansend`` fails; the
        raised exception includes the failed shell command.
        """
        normalized: dict[Joint, int] = {}
        for key, value in positions.items():
            selected = parse_joint(key)
            if selected in normalized:
                raise ValueError(f"joint {selected.name} was specified twice")
            normalized[selected] = value
        missing = set(Joint) - set(normalized)
        if missing:
            names = ", ".join(joint.name for joint in sorted(missing))
            raise ValueError(f"pose is missing joints: {names}")
        frames = [
            encode_cansend(joint, normalized[joint], self.interface) for joint in Joint
        ]
        commands = [
            f"cansend {shlex.quote(self.interface)} {shlex.quote(frame)}"
            for frame in frames
        ]
        return self.board.run(" && ".join(commands))

    def perform(self, name: str, library: PoseLibrary) -> CommandResult:
        """Load a named static pose and transmit its six joint positions once.

        ``library`` must have been loaded with PoseLibrary.from_file. Unknown
        names and connection errors are propagated without suppression.
        """
        return self.set_pose(library.get(name))
