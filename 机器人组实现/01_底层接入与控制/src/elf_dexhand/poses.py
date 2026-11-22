"""Load complete, validated static hand poses from YAML."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

from .protocol import Joint, parse_joint, validate_position


class PoseFormatError(ValueError):
    """A YAML pose file has invalid syntax, duplicates, or missing joints."""


class _UniqueSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects repeated mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueSafeLoader, node: yaml.MappingNode
) -> dict[object, object]:
    """Construct a YAML mapping and report duplicate keys with their location."""
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise PoseFormatError("YAML mapping keys must be scalar") from exc
        if duplicate:
            raise PoseFormatError(
                f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}"
            )
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


@dataclass(frozen=True)
class PoseLibrary:
    """Named six-joint poses loaded and validated at construction time."""

    poses: Mapping[str, Mapping[Joint, int]]

    @classmethod
    def default(cls) -> PoseLibrary:
        """Load the example poses bundled with this installed package."""
        path = Path(__file__).parent / "data" / "default_poses.yaml"
        return cls.from_file(path)

    @classmethod
    def from_file(cls, path: str | Path) -> PoseLibrary:
        """Read a YAML file without executing code or moving the hand.

        All six joints must appear exactly once in each pose. File, YAML, and
        schema errors raise OSError or PoseFormatError with useful context.
        """
        source = Path(path)
        try:
            with source.open("r", encoding="utf-8") as stream:
                data = yaml.load(stream, Loader=_UniqueSafeLoader)
        except yaml.YAMLError as exc:
            raise PoseFormatError(f"invalid YAML in {source}: {exc}") from exc
        if not isinstance(data, dict) or set(data) != {"poses"}:
            raise PoseFormatError("top level must contain only a 'poses' mapping")
        raw_poses = data["poses"]
        if not isinstance(raw_poses, dict) or not raw_poses:
            raise PoseFormatError("'poses' must be a nonempty mapping")

        parsed: dict[str, dict[Joint, int]] = {}
        for name, raw_pose in raw_poses.items():
            if not isinstance(name, str) or not name.strip():
                raise PoseFormatError("pose names must be nonempty strings")
            if not isinstance(raw_pose, dict):
                raise PoseFormatError(f"pose {name!r} must be a mapping")
            positions: dict[Joint, int] = {}
            for key, value in raw_pose.items():
                try:
                    joint = parse_joint(key)
                    if joint in positions:
                        raise PoseFormatError(
                            f"pose {name!r} names {joint.name} more than once"
                        )
                    positions[joint] = validate_position(joint, value)
                except (TypeError, ValueError) as exc:
                    raise PoseFormatError(f"pose {name!r}: {exc}") from exc
            missing = set(Joint) - set(positions)
            if missing:
                names = ", ".join(joint.name for joint in sorted(missing))
                raise PoseFormatError(f"pose {name!r} is missing: {names}")
            parsed[name] = positions
        return cls(poses=parsed)

    def get(self, name: str) -> Mapping[Joint, int]:
        """Return a pose by name or raise KeyError with available names."""
        try:
            return self.poses[name]
        except KeyError as exc:
            available = ", ".join(self.poses)
            raise KeyError(f"unknown pose {name!r}; available: {available}") from exc
