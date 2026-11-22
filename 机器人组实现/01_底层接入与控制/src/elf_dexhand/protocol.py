"""Encode six-joint positions as SocketCAN ``cansend`` frames."""

from __future__ import annotations

import re
from enum import IntEnum


class Joint(IntEnum):
    """CAN identifiers for the six independently controlled joints."""

    THUMB_SPREAD = 1
    THUMB_BEND = 2
    INDEX = 3
    MIDDLE = 4
    RING = 5
    PINKY = 6


JOINT_ALIASES: dict[str, Joint] = {
    "thumb_spread": Joint.THUMB_SPREAD,
    "拇指侧摆": Joint.THUMB_SPREAD,
    "thumb_bend": Joint.THUMB_BEND,
    "拇指弯曲": Joint.THUMB_BEND,
    "index": Joint.INDEX,
    "食指": Joint.INDEX,
    "middle": Joint.MIDDLE,
    "中指": Joint.MIDDLE,
    "ring": Joint.RING,
    "无名指": Joint.RING,
    "pinky": Joint.PINKY,
    "小指": Joint.PINKY,
}

JOINT_LIMITS: dict[Joint, int] = {
    Joint.THUMB_SPREAD: 2000,
    Joint.THUMB_BEND: 4095,
    Joint.INDEX: 4095,
    Joint.MIDDLE: 4095,
    Joint.RING: 4095,
    Joint.PINKY: 4095,
}

_INTERFACE_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*\Z")


def parse_joint(joint: Joint | int | str) -> Joint:
    """Resolve a CAN ID or English/Chinese name to a joint.

    Unknown or ambiguous names raise ValueError; bool is rejected even though
    it is an int subclass. This function has no side effects.
    """
    if isinstance(joint, bool):
        raise ValueError("joint must be an ID from 1 to 6 or a joint name")
    if isinstance(joint, Joint):
        return joint
    if isinstance(joint, int):
        try:
            return Joint(joint)
        except ValueError as exc:
            raise ValueError(f"joint ID must be 1 to 6: {joint}") from exc
    if isinstance(joint, str):
        key = joint.strip().lower()
        if key in JOINT_ALIASES:
            return JOINT_ALIASES[key]
        if key.isascii() and key.isdecimal():
            return parse_joint(int(key))
    raise ValueError(f"unknown joint: {joint!r}")


def validate_position(joint: Joint, position: int) -> int:
    """Return a valid position or raise instead of silently clamping it."""
    if isinstance(position, bool) or not isinstance(position, int):
        raise TypeError(f"position for {joint.name} must be an integer")
    maximum = JOINT_LIMITS[joint]
    if not 0 <= position <= maximum:
        raise ValueError(
            f"position for {joint.name} must be 0 to {maximum}: {position}"
        )
    return position


def validate_interface(interface: str) -> str:
    """Validate a Linux CAN interface name before composing a shell command."""
    if not isinstance(interface, str) or not _INTERFACE_PATTERN.fullmatch(interface):
        raise ValueError(f"invalid CAN interface name: {interface!r}")
    return interface


def encode_cansend(
    joint: Joint | int | str, position: int, interface: str = "can0"
) -> str:
    """Build a ``cansend`` argument such as ``003#AAD007E809640300``.

    The interface is validated but omitted from the returned frame argument.
    The caller passes it separately to ``cansend``. No frame is transmitted.
    """
    validate_interface(interface)
    selected = parse_joint(joint)
    value = validate_position(selected, position)
    return f"{selected.value:03X}#AA{value & 0xFF:02X}{value >> 8:02X}E809640300"
