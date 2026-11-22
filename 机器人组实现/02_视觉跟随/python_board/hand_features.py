"""Pure, uncalibrated XY measurements for a six-channel no-motion preview.

measure_hand(frame_or_values) accepts a parser frame dict or a 63-value list/
tuple. It returns a new dict with input_valid, received_ns, quality_flags and
channels. No I/O occurs; only math is imported. No smoothing, mapping or motor
position is calculated. Every channel's motor and predicted_position are None.

Five dimensionless curl candidates use 1 - endpoint distance / polyline length
on the first two components of four candidate points. valid means only that
this projected numeric geometry is usable; it does NOT mean calibrated or safe
for control. It does not establish physical XY scale, point anatomy or 3D angle.

Zero/numerically collapsed segments and collapsed end-to-end projections are
low quality and invalid, even when a raw ratio can be calculated. The relative
1e-9 threshold detects numerical collapse, not a measured tracking-confidence
threshold. It preserves uniform-scale invariance.

The sixth value is a signed diagnostic angle from palm axis 0->9 to thumb base
axis 1->2, in degrees. It ALWAYS has valid=False and uncalibrated_coupled. The
current captured thumb features have not separated flexion from lateral swing.
"""

import math


NUMERICAL_COLLAPSE_RATIO = 1e-9
CHAINS = (
    ("thumb_curl", (1, 2, 3, 4)),
    ("index_curl", (5, 6, 7, 8)),
    ("middle_curl", (9, 10, 11, 12)),
    ("ring_curl", (13, 14, 15, 16)),
    ("little_curl", (17, 18, 19, 20)),
)
COMMON_FLAGS = ("xy_projection_only", "not_calibrated", "no_motor_targets")


def _channel(point_ids):
    return {"raw": None, "valid": False, "quality_flags": list(COMMON_FLAGS),
            "point_ids_zero_based": point_ids, "motor": None,
            "predicted_position": None}


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _curl(points, ids):
    result = _channel(ids)
    p = [points[index] for index in ids]
    lengths = [_distance(p[i], p[i + 1]) for i in range(3)]
    span = _distance(p[0], p[-1])
    total = sum(lengths)
    if not all(math.isfinite(value) for value in lengths + [span, total]):
        result["quality_flags"].extend(("low_quality", "numeric_overflow"))
        return result
    result["polyline_length_xy"] = total
    result["endpoint_distance_xy"] = span
    if total <= 0.0:
        result["quality_flags"].extend(("low_quality", "zero_total_length", "collapsed_chain"))
        return result

    raw = 1.0 - span / total
    if not math.isfinite(raw) or raw < -1e-12 or raw > 1.0 + 1e-12:
        result["quality_flags"].extend(("low_quality", "inconsistent_geometry"))
        return result
    # Triangle-inequality roundoff only; this is not a calibrated motion limit.
    result["raw"] = min(1.0, max(0.0, raw))
    maximum = max(lengths)
    threshold = maximum * NUMERICAL_COLLAPSE_RATIO
    flags = []
    if any(value == 0.0 for value in lengths):
        flags.append("zero_length_segment")
    elif any(value <= threshold for value in lengths):
        flags.append("collapsed_segment")
    if span <= threshold:
        flags.append("collapsed_end_to_end")
    if flags:
        result["quality_flags"].extend(["low_quality"] + flags)
        return result
    result["valid"] = True
    return result


def _thumb_diagnostic(points):
    result = _channel((1, 2, 0, 9))
    result["quality_flags"].append("uncalibrated_coupled")
    result["unit"] = "degrees_diagnostic_only"
    thumb = (points[2][0] - points[1][0], points[2][1] - points[1][1])
    palm = (points[9][0] - points[0][0], points[9][1] - points[0][1])
    u = math.hypot(*thumb)
    v = math.hypot(*palm)
    if not math.isfinite(u) or not math.isfinite(v):
        result["quality_flags"].extend(("low_quality", "numeric_overflow"))
        return result
    if min(u, v) <= max(u, v) * NUMERICAL_COLLAPSE_RATIO:
        result["quality_flags"].extend(("low_quality", "collapsed_reference_axis"))
        return result
    ux, uy = thumb[0] / u, thumb[1] / u
    vx, vy = palm[0] / v, palm[1] / v
    result["raw"] = math.degrees(math.atan2(vx * uy - vy * ux, vx * ux + vy * uy))
    # Deliberately remains False even with finite, nondegenerate diagnostics.
    return result


def measure_hand(frame_or_values):
    """Create a fresh, no-motion measurement snapshot; never mutate the input.

    Invalid source data returns all six channels invalid, rather than publishing
    a partially updated hand. received_ns is copied only when it is a valid
    integer; it is a local parser receive time, never an image timestamp.
    """
    received_ns = None
    if isinstance(frame_or_values, dict):
        values = frame_or_values.get("values")
        candidate_time = frame_or_values.get("received_ns")
        if type(candidate_time) is int and candidate_time >= 0:
            received_ns = candidate_time
    else:
        values = frame_or_values
    result = {"input_valid": False, "received_ns": received_ns,
              "quality_flags": list(COMMON_FLAGS), "channels": {}}
    if received_ns is None:
        result["quality_flags"].append("receive_time_unavailable")
    reason = None
    if not isinstance(values, (tuple, list)) or len(values) != 63:
        reason = "invalid_value_count_or_container"
    else:
        copied = []
        for value in values:
            if type(value) not in (int, float):
                reason = "non_numeric_input"
                break
            try:
                converted = float(value)
            except (ValueError, OverflowError):
                reason = "non_finite_input"
                break
            if not math.isfinite(converted):
                reason = "non_finite_input"
                break
            copied.append(converted)
    if reason is not None:
        result["quality_flags"].append(reason)
        for name, ids in CHAINS:
            channel = _channel(ids)
            channel["quality_flags"].extend(("low_quality", reason))
            result["channels"][name] = channel
        diagnostic = _channel((1, 2, 0, 9))
        diagnostic["unit"] = "degrees_diagnostic_only"
        diagnostic["quality_flags"].extend(("low_quality", reason, "uncalibrated_coupled"))
        result["channels"]["thumb_spread_diagnostic"] = diagnostic
        return result

    points = [(copied[i], copied[i + 1]) for i in range(0, 63, 3)]
    result["input_valid"] = True
    for name, ids in CHAINS:
        result["channels"][name] = _curl(points, ids)
    result["channels"]["thumb_spread_diagnostic"] = _thumb_diagnostic(points)
    return result
