"""Derive per-user camera mapping endpoints from recorded hand poses."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence

from .protocol import Joint
from .vision_mapping import MappingProfile

STAGES = ("open", "fist", "thumb_wide", "thumb_tucked", "thumb_bent")
SPREAD_CANDIDATES = (
    "tip_index_ratio",
    "palm_plane_angle",
    "palm_abduction_angle",
)
BEND_CANDIDATES = ("mcp_angle", "ip_angle", "wrist_tip_angle")


def _quantile(values: Sequence[float], fraction: float) -> float:
    """Return a deterministic nearest-rank quantile for calibration samples."""
    ordered = sorted(values)
    index = round((len(ordered) - 1) * fraction)
    return ordered[index]


def _endpoints(
    opened: Sequence[float], closed: Sequence[float], minimum_gap: float
) -> tuple[float, float, float]:
    """Find robust endpoints and a separation-to-noise score.

    Both sample sets must be nonempty and finite. Raises ValueError when a
    gesture pair cannot be distinguished reliably from landmark jitter.
    """
    if not opened or not closed:
        raise ValueError("calibration stages must contain valid hand samples")
    if not all(math.isfinite(value) for value in (*opened, *closed)):
        raise ValueError("calibration samples must be finite")
    open_median = statistics.median(opened)
    closed_median = statistics.median(closed)
    open_noise = statistics.median(abs(value - open_median) for value in opened)
    closed_noise = statistics.median(abs(value - closed_median) for value in closed)
    separation = abs(open_median - closed_median)
    noise = open_noise + closed_noise
    if separation < max(minimum_gap, 3 * noise):
        raise ValueError(
            f"gesture separation {separation:.3f} is too small for noise {noise:.3f}"
        )
    if open_median > closed_median:
        return (
            _quantile(opened, 0.1),
            _quantile(closed, 0.9),
            separation / (noise + minimum_gap / 2),
        )
    return (
        _quantile(opened, 0.9),
        _quantile(closed, 0.1),
        separation / (noise + minimum_gap / 2),
    )


def _select_metric(
    samples: Mapping[str, Sequence[Mapping[str, float]]],
    open_stage: str,
    closed_stage: str,
    metrics: Sequence[str],
    minimum_gaps: Mapping[str, float],
    reference_stage: str | None = None,
) -> tuple[str, float, float]:
    """Select the thumb feature that best separates two demonstrated poses."""
    options: list[tuple[float, str, float, float]] = []
    failures: list[str] = []
    for metric in metrics:
        opened = [row[metric] for row in samples[open_stage]]
        closed = [row[metric] for row in samples[closed_stage]]
        try:
            open_value, closed_value, score = _endpoints(
                opened, closed, minimum_gaps[metric]
            )
        except ValueError as exc:
            failures.append(f"{metric}: {exc}")
            continue
        if reference_stage is not None:
            reference = statistics.median(
                row[metric] for row in samples[reference_stage]
            )
            fraction = (reference - open_value) / (closed_value - open_value)
            if fraction > 0.5:
                failures.append(
                    f"{metric}: normal open hand maps {fraction:.2f} "
                    "of the way toward the tucked position"
                )
                continue
        options.append((score, metric, open_value, closed_value))
    if not options:
        detail = "; ".join(failures)
        raise ValueError(f"cannot calibrate thumb movement: {detail}")
    _, metric, open_value, closed_value = max(options)
    return metric, open_value, closed_value


def derive_mapping(
    samples: Mapping[str, Sequence[Mapping[str, float]]],
    base: MappingProfile,
) -> dict[str, object]:
    """Build a six-joint YAML document from five demonstrated hand poses.

    This function has no hardware or file side effects. It preserves the
    base profile's motor position caps but derives all image measurements and
    both thumb feature choices from the samples. Missing or weakly separated
    gestures raise ValueError instead of producing an unsafe mapping.
    """
    if set(samples) != set(STAGES):
        raise ValueError(f"calibration requires stages: {', '.join(STAGES)}")
    for stage in STAGES:
        if len(samples[stage]) < 20:
            raise ValueError(f"{stage} needs at least 20 valid hand samples")
    spread_metric, spread_open, spread_closed = validate_thumb_spread(samples)
    bend_metric, bend_open, bend_closed = validate_thumb_bend(samples)
    finger_endpoints = validate_four_fingers(samples)
    spread = base.spread
    mapping: dict[str, object] = {
        "thumb_spread": {
            "metric": spread_metric,
            "open_value": round(spread_open, 4),
            "closed_value": round(spread_closed, 4),
            "open_position": spread.open_position,
            "closed_position": spread.closed_position,
        }
    }
    for joint in Joint:
        if joint is Joint.THUMB_SPREAD:
            continue
        if joint is Joint.THUMB_BEND:
            open_angle, closed_angle = bend_open, bend_closed
        else:
            open_angle, closed_angle = finger_endpoints[joint.name.lower()]
        calibration = base.angles[joint]
        entry: dict[str, object] = {
            "open_angle": round(max(0.0, min(180.0, open_angle)), 2),
            "closed_angle": round(max(0.0, min(180.0, closed_angle)), 2),
            "open_position": calibration.open_position,
            "closed_position": calibration.closed_position,
        }
        if joint is Joint.THUMB_BEND:
            entry = {"metric": bend_metric, **entry}
        mapping[joint.name.lower()] = entry
    return {"camera_mapping": mapping}


def validate_thumb_spread(
    samples: Mapping[str, Sequence[Mapping[str, float]]],
) -> tuple[str, float, float]:
    """Require side-swing poses that also classify a normal open hand as open."""
    return _select_metric(
        samples,
        "thumb_wide",
        "thumb_tucked",
        SPREAD_CANDIDATES,
        {
            "tip_index_ratio": 0.08,
            "palm_plane_angle": 5.0,
            "palm_abduction_angle": 10.0,
        },
        reference_stage="open",
    )


def validate_thumb_bend(
    samples: Mapping[str, Sequence[Mapping[str, float]]],
) -> tuple[str, float, float]:
    """Require a thumb-only bend distinguishable from the open hand."""
    return _select_metric(
        samples,
        "open",
        "thumb_bent",
        BEND_CANDIDATES,
        {metric: 10.0 for metric in BEND_CANDIDATES},
    )


def validate_four_fingers(
    samples: Mapping[str, Sequence[Mapping[str, float]]],
) -> dict[str, tuple[float, float]]:
    """Require each finger in the fist to differ clearly from the open hand."""
    endpoints: dict[str, tuple[float, float]] = {}
    for joint in (Joint.INDEX, Joint.MIDDLE, Joint.RING, Joint.PINKY):
        metric = joint.name.lower()
        open_values = [row[metric] for row in samples["open"]]
        fist_values = [row[metric] for row in samples["fist"]]
        try:
            opened, closed, _ = _endpoints(
                open_values,
                fist_values,
                20.0,
            )
        except ValueError as exc:
            raise ValueError(f"{metric}: {exc}") from exc
        if statistics.median(open_values) <= statistics.median(fist_values):
            raise ValueError(
                f"{metric}: fist angle must be smaller than straight-hand angle"
            )
        endpoints[metric] = opened, closed
    return endpoints
