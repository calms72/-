"""Map MediaPipe's 21 world landmarks to six bounded motor positions."""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

import yaml

from .poses import _UniqueSafeLoader
from .protocol import Joint, validate_position

DEFAULT_CALIBRATION_PATH = Path("configs/camera_mapping_calibrated.yaml")


class Landmark3D(Protocol):
    """Coordinates required from a MediaPipe world landmark."""

    x: float
    y: float
    z: float


@dataclass(frozen=True)
class AngleCalibration:
    """Two measured angles and their corresponding motor positions."""

    open_angle: float
    closed_angle: float
    open_position: int
    closed_position: int


@dataclass(frozen=True)
class SpreadCalibration:
    """Two measured thumb side-swing values and motor positions."""

    metric: str
    open_value: float
    closed_value: float
    open_position: int
    closed_position: int


@dataclass(frozen=True)
class MappingProfile:
    """Calibration data for all six motors."""

    angles: Mapping[Joint, AngleCalibration]
    spread: SpreadCalibration
    thumb_bend_metric: str = "mcp_angle"

    @classmethod
    def default(cls) -> MappingProfile:
        """Load the package's conservative starting calibration."""
        return cls.from_file(Path(__file__).parent / "data" / "vision_mapping.yaml")

    @classmethod
    def from_file(cls, path: str | Path) -> MappingProfile:
        """Read, type-check and bound all mapping endpoints from YAML.

        No camera or board action occurs. File errors propagate; malformed
        YAML or invalid calibration values raise ValueError with context.
        """
        source = Path(path)
        try:
            with source.open("r", encoding="utf-8") as stream:
                data = yaml.load(stream, Loader=_UniqueSafeLoader)
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid mapping YAML in {source}: {exc}") from exc
        if not isinstance(data, dict) or set(data) != {"camera_mapping"}:
            raise ValueError("mapping file must contain only 'camera_mapping'")
        raw = data["camera_mapping"]
        expected = {"thumb_spread", "thumb_bend", "index", "middle", "ring", "pinky"}
        if not isinstance(raw, dict) or set(raw) != expected:
            raise ValueError(f"camera_mapping must contain exactly: {sorted(expected)}")
        raw_spread = raw["thumb_spread"]
        spread_keys = {
            "metric",
            "open_value",
            "closed_value",
            "open_position",
            "closed_position",
        }
        if not isinstance(raw_spread, dict) or set(raw_spread) != spread_keys:
            raise ValueError(
                "thumb_spread requires metric, value and position endpoints"
            )
        spread_metric = raw_spread["metric"]
        if not isinstance(spread_metric, str) or spread_metric not in {
            "tip_index_ratio",
            "palm_plane_angle",
            "palm_abduction_angle",
        }:
            raise ValueError(f"unknown thumb spread metric: {spread_metric!r}")
        spread = SpreadCalibration(
            metric=spread_metric,
            open_value=_finite(raw_spread["open_value"], "open_value"),
            closed_value=_finite(raw_spread["closed_value"], "closed_value"),
            open_position=validate_position(
                Joint.THUMB_SPREAD, raw_spread["open_position"]
            ),
            closed_position=validate_position(
                Joint.THUMB_SPREAD, raw_spread["closed_position"]
            ),
        )
        if spread.open_value == spread.closed_value:
            raise ValueError("thumb spread endpoints must differ")

        angles: dict[Joint, AngleCalibration] = {}
        angle_keys = {"open_angle", "closed_angle", "open_position", "closed_position"}
        thumb_keys = angle_keys | {"metric"}
        thumb_metric = "mcp_angle"
        for joint in Joint:
            if joint is Joint.THUMB_SPREAD:
                continue
            values = raw[joint.name.lower()]
            required = thumb_keys if joint is Joint.THUMB_BEND else angle_keys
            if not isinstance(values, dict) or set(values) != required:
                raise ValueError(f"{joint.name} requires angle and position endpoints")
            if joint is Joint.THUMB_BEND:
                thumb_metric = values["metric"]
                if not isinstance(thumb_metric, str) or thumb_metric not in {
                    "mcp_angle",
                    "ip_angle",
                    "wrist_tip_angle",
                }:
                    raise ValueError(f"unknown thumb bend metric: {thumb_metric!r}")
            calibration = AngleCalibration(
                open_angle=_finite(values["open_angle"], f"{joint.name}.open_angle"),
                closed_angle=_finite(
                    values["closed_angle"], f"{joint.name}.closed_angle"
                ),
                open_position=validate_position(joint, values["open_position"]),
                closed_position=validate_position(joint, values["closed_position"]),
            )
            if not 0 <= calibration.open_angle <= 180:
                raise ValueError(f"{joint.name} open_angle must be 0..180")
            if not 0 <= calibration.closed_angle <= 180:
                raise ValueError(f"{joint.name} closed_angle must be 0..180")
            if calibration.open_angle == calibration.closed_angle:
                raise ValueError(f"{joint.name} angle endpoints must differ")
            angles[joint] = calibration
        return cls(angles=angles, spread=spread, thumb_bend_metric=thumb_metric)


def _finite(value: object, label: str) -> float:
    """Convert a numeric YAML value while rejecting booleans and NaN."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _distance(a: Landmark3D, b: Landmark3D) -> float:
    """Return the Euclidean distance between two world landmarks."""
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def _angle(a: Landmark3D, b: Landmark3D, c: Landmark3D) -> float:
    """Return the interior angle at b in degrees; reject collapsed segments."""
    first = (a.x - b.x, a.y - b.y, a.z - b.z)
    second = (c.x - b.x, c.y - b.y, c.z - b.z)
    norm1 = math.sqrt(sum(value * value for value in first))
    norm2 = math.sqrt(sum(value * value for value in second))
    if norm1 < 1e-6 or norm2 < 1e-6:
        raise ValueError("hand landmarks contain a collapsed finger segment")
    cosine = sum(x * y for x, y in zip(first, second)) / (norm1 * norm2)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def _palm_plane_angle(landmarks: Sequence[Landmark3D]) -> float:
    """Measure the thumb ray's absolute angle away from the palm plane."""
    wrist, index, pinky = landmarks[0], landmarks[5], landmarks[17]
    first = (index.x - wrist.x, index.y - wrist.y, index.z - wrist.z)
    second = (pinky.x - wrist.x, pinky.y - wrist.y, pinky.z - wrist.z)
    normal = (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )
    base, tip = landmarks[1], landmarks[4]
    ray = (tip.x - base.x, tip.y - base.y, tip.z - base.z)
    magnitude = math.sqrt(sum(value * value for value in normal)) * math.sqrt(
        sum(value * value for value in ray)
    )
    if magnitude < 1e-9:
        raise ValueError("hand landmarks cannot define a palm plane")
    sine = abs(sum(x * y for x, y in zip(ray, normal))) / magnitude
    return math.degrees(math.asin(max(0.0, min(1.0, sine))))


def _palm_abduction_angle(landmarks: Sequence[Landmark3D]) -> float:
    """Measure the thumb's in-plane angle from the middle-finger direction.

    Projecting onto the palm plane reduces the effect of rotating the whole
    hand toward the camera. A degenerate palm or projected thumb raises
    ValueError instead of supplying a motor target.
    """
    wrist, index, middle, pinky = (
        landmarks[0], landmarks[5], landmarks[9], landmarks[17]
    )
    first = (index.x - wrist.x, index.y - wrist.y, index.z - wrist.z)
    second = (pinky.x - wrist.x, pinky.y - wrist.y, pinky.z - wrist.z)
    normal = (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )
    normal_length = math.sqrt(sum(value * value for value in normal))
    if normal_length < 1e-9:
        raise ValueError("hand landmarks cannot define a palm plane")
    unit_normal = tuple(value / normal_length for value in normal)
    base, tip = landmarks[1], landmarks[4]
    thumb = (tip.x - base.x, tip.y - base.y, tip.z - base.z)
    forward = (middle.x - wrist.x, middle.y - wrist.y, middle.z - wrist.z)

    def project(vector: tuple[float, float, float]) -> tuple[float, float, float]:
        """Remove the vector component normal to the palm plane."""
        normal_component = sum(
            value * normal_value
            for value, normal_value in zip(vector, unit_normal)
        )
        return tuple(
            value - normal_component * normal_value
            for value, normal_value in zip(vector, unit_normal)
        )

    projected_thumb = project(thumb)
    projected_forward = project(forward)
    magnitude = math.sqrt(
        sum(value * value for value in projected_thumb)
        * sum(value * value for value in projected_forward)
    )
    if magnitude < 1e-9:
        raise ValueError("hand landmarks cannot define thumb abduction")
    cosine = sum(
        a * b for a, b in zip(projected_thumb, projected_forward)
    ) / magnitude
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def _map(value: float, opened: float, closed: float, low: int, high: int) -> int:
    """Linearly map a measured quantity and clamp it to configured endpoints."""
    fraction = (value - opened) / (closed - opened)
    fraction = max(0.0, min(1.0, fraction))
    return round(low + fraction * (high - low))


def measure_thumb_candidates(landmarks: Sequence[Landmark3D]) -> dict[str, float]:
    """Return candidate thumb features for data-driven metric selection.

    Requires 21 finite world landmarks. Degenerate palm or finger geometry
    raises ValueError; no hardware action occurs.
    """
    _validate_landmarks(landmarks)
    palm_width = _distance(landmarks[5], landmarks[17])
    if palm_width < 1e-6:
        raise ValueError("hand landmarks have zero palm width")
    return {
        "tip_index_ratio": _distance(landmarks[4], landmarks[5]) / palm_width,
        "palm_plane_angle": _palm_plane_angle(landmarks),
        "palm_abduction_angle": _palm_abduction_angle(landmarks),
        "mcp_angle": _angle(landmarks[1], landmarks[2], landmarks[3]),
        "ip_angle": _angle(landmarks[2], landmarks[3], landmarks[4]),
        "wrist_tip_angle": _angle(landmarks[0], landmarks[3], landmarks[4]),
    }


def _validate_landmarks(landmarks: Sequence[Landmark3D]) -> None:
    """Reject missing or nonfinite camera points before measuring geometry."""
    if len(landmarks) != 21:
        raise ValueError(f"expected 21 landmarks, got {len(landmarks)}")
    for point in landmarks:
        if not all(math.isfinite(value) for value in (point.x, point.y, point.z)):
            raise ValueError("hand landmarks contain nonfinite coordinates")


def measure_hand(
    landmarks: Sequence[Landmark3D], profile: MappingProfile | None = None
) -> dict[Joint, float]:
    """Measure six features using a profile's selected thumb geometry.

    Requires 21 finite world landmarks in MediaPipe order. Raises ValueError
    for incomplete or degenerate detections. No board action occurs.
    """
    _validate_landmarks(landmarks)
    spread_metric = profile.spread.metric if profile else "tip_index_ratio"
    bend_metric = profile.thumb_bend_metric if profile else "mcp_angle"
    if spread_metric == "tip_index_ratio":
        palm_width = _distance(landmarks[5], landmarks[17])
        if palm_width < 1e-6:
            raise ValueError("hand landmarks have zero palm width")
        spread_value = _distance(landmarks[4], landmarks[5]) / palm_width
    elif spread_metric == "palm_plane_angle":
        spread_value = _palm_plane_angle(landmarks)
    elif spread_metric == "palm_abduction_angle":
        spread_value = _palm_abduction_angle(landmarks)
    else:
        raise ValueError(f"unknown thumb spread metric: {spread_metric}")
    thumb_points = {
        "mcp_angle": (1, 2, 3),
        "ip_angle": (2, 3, 4),
        "wrist_tip_angle": (0, 3, 4),
    }
    if bend_metric not in thumb_points:
        raise ValueError(f"unknown thumb bend metric: {bend_metric}")
    a, b, c = thumb_points[bend_metric]
    measurements = {
        Joint.THUMB_SPREAD: spread_value,
        Joint.THUMB_BEND: _angle(landmarks[a], landmarks[b], landmarks[c]),
    }
    angle_points = {
        Joint.INDEX: (5, 6, 7),
        Joint.MIDDLE: (9, 10, 11),
        Joint.RING: (13, 14, 15),
        Joint.PINKY: (17, 18, 19),
    }
    for joint, (a, b, c) in angle_points.items():
        measurements[joint] = _angle(landmarks[a], landmarks[b], landmarks[c])
    return measurements


def estimate_pose(
    landmarks: Sequence[Landmark3D], profile: MappingProfile
) -> dict[Joint, int]:
    """Map one detected hand to six bounded targets without sending CAN.

    Raises ValueError for incomplete or degenerate landmarks. The mapping
    clamps all values to the profile's motor position endpoints.
    """
    measurements = measure_hand(landmarks, profile)
    spread = profile.spread
    result = {
        Joint.THUMB_SPREAD: _map(
            measurements[Joint.THUMB_SPREAD],
            spread.open_value,
            spread.closed_value,
            spread.open_position,
            spread.closed_position,
        )
    }
    for joint in profile.angles:
        calibration = profile.angles[joint]
        result[joint] = _map(
            measurements[joint],
            calibration.open_angle,
            calibration.closed_angle,
            calibration.open_position,
            calibration.closed_position,
        )
    for joint, position in result.items():
        validate_position(joint, position)
    return result


class MotionFilter:
    """Smooth target poses and limit each motor's change per publish step."""

    def __init__(
        self,
        initial: Mapping[Joint, int],
        alpha: float = 0.5,
        max_step: int = 250,
    ) -> None:
        """Start from a validated pose; no board action occurs."""
        if not 0 < alpha <= 1:
            raise ValueError("alpha must be in (0, 1]")
        if max_step <= 0:
            raise ValueError("max_step must be positive")
        if set(initial) != set(Joint):
            raise ValueError("initial pose must contain all six joints")
        self._current = {
            joint: validate_position(joint, initial[joint]) for joint in Joint
        }
        self.alpha = alpha
        self.max_step = max_step

    def update(self, target: Mapping[Joint, int]) -> dict[Joint, int]:
        """Return an in-memory, limited pose; reject invalid input unchanged."""
        if set(target) != set(Joint):
            raise ValueError("target pose must contain all six joints")
        checked = {joint: validate_position(joint, target[joint]) for joint in Joint}
        next_pose: dict[Joint, int] = {}
        for joint in Joint:
            previous = self._current[joint]
            smoothed = previous + self.alpha * (checked[joint] - previous)
            change = max(-self.max_step, min(self.max_step, round(smoothed - previous)))
            next_pose[joint] = validate_position(joint, previous + change)
        self._current = next_pose
        return dict(next_pose)

    def reset(self, pose: Mapping[Joint, int]) -> None:
        """Replace the internal pose after a safe-state command succeeds."""
        if set(pose) != set(Joint):
            raise ValueError("reset pose must contain all six joints")
        self._current = {
            joint: validate_position(joint, pose[joint]) for joint in Joint
        }


class PoseTargetStabilizer:
    """Reject one-frame pose spikes, then smooth all six motor targets.

    An odd-width median suppresses isolated landmark jumps. The following
    low-pass motion filter reduces frame-to-frame jitter while keeping each
    target update bounded. Hardware trajectory limits remain a separate layer.
    """

    def __init__(
        self,
        initial: Mapping[Joint, int],
        *,
        window_size: int = 5,
        alpha: float = 0.25,
        max_step: int = 250,
    ) -> None:
        if set(initial) != set(Joint):
            raise ValueError("initial pose must contain all six joints")
        if window_size < 3 or window_size % 2 == 0:
            raise ValueError("window_size must be an odd integer of at least 3")
        checked = {joint: validate_position(joint, initial[joint]) for joint in Joint}
        self._history = {
            joint: deque([checked[joint]] * window_size, maxlen=window_size)
            for joint in Joint
        }
        self.window_size = window_size
        self._motion = MotionFilter(checked, alpha=alpha, max_step=max_step)

    def update(self, target: Mapping[Joint, int]) -> dict[Joint, int]:
        """Return a median and low-pass filtered pose without transmitting it."""
        if set(target) != set(Joint):
            raise ValueError("target pose must contain all six joints")
        checked = {joint: validate_position(joint, target[joint]) for joint in Joint}
        median_pose: dict[Joint, int] = {}
        for joint in Joint:
            history = self._history[joint]
            history.append(checked[joint])
            median_pose[joint] = round(statistics.median(history))
        return self._motion.update(median_pose)

    def reset(self, pose: Mapping[Joint, int]) -> None:
        """Rebase the filter after arming/disarming or a safe-pose transition."""
        if set(pose) != set(Joint):
            raise ValueError("reset pose must contain all six joints")
        checked = {joint: validate_position(joint, pose[joint]) for joint in Joint}
        for joint in Joint:
            self._history[joint] = deque(
                [checked[joint]] * self.window_size, maxlen=self.window_size
            )
        self._motion.reset(checked)


class ThumbTargetStabilizer:
    """Reject isolated thumb landmark spikes while retaining fast gestures.

    A three-frame median removes one-frame tracking outliers. Small sustained
    changes receive stronger smoothing; large sustained movements receive a
    faster response. Four non-thumb joints pass through unchanged.
    """

    def __init__(self, initial: Mapping[Joint, int]) -> None:
        """Initialize two thumb filters from a validated six-joint pose."""
        if set(initial) != set(Joint):
            raise ValueError("initial pose must contain all six joints")
        checked = {joint: validate_position(joint, initial[joint]) for joint in Joint}
        self._history = {
            joint: deque([checked[joint]] * 3, maxlen=3)
            for joint in (Joint.THUMB_SPREAD, Joint.THUMB_BEND)
        }
        self._current = {
            joint: float(checked[joint])
            for joint in (Joint.THUMB_SPREAD, Joint.THUMB_BEND)
        }

    def update(self, target: Mapping[Joint, int]) -> dict[Joint, int]:
        """Return a stabilized pose without transmitting or altering fingers."""
        if set(target) != set(Joint):
            raise ValueError("target pose must contain all six joints")
        checked = {joint: validate_position(joint, target[joint]) for joint in Joint}
        for joint in (Joint.THUMB_SPREAD, Joint.THUMB_BEND):
            history = self._history[joint]
            history.append(checked[joint])
            median = statistics.median(history)
            difference = median - self._current[joint]
            if abs(difference) > 25:
                alpha = 0.85 if abs(difference) >= 250 else 0.35
                self._current[joint] += alpha * difference
            checked[joint] = validate_position(joint, round(self._current[joint]))
        return checked
