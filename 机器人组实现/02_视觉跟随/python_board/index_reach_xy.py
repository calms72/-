"""Independent, uncalibrated index reach: |p8-p0|xy / |p9-p0|xy.

Only XY of zero-based points 0/8/9 is used. Z and the short 5->6 segment are
not reconstructed or inferred. This is a projected distance ratio, not a joint
angle, hand identity/confidence result, motion qualification or mapping. Raw
may exceed 1. All motor/prediction fields and other channels stay None.

The existing visual_protocol.py parser requires exactly two fractional digits
on the wire. GRID_STEP_XY=0.01 denotes that observed text grid, without claiming
rounding/truncation semantics, physical units, an error bound or confidence.
Diagnostics evaluate +/- one grid step on each of the six used coordinates;
they never impose a new quality rejection threshold. Basic numeric validity
requires only the used finite XY, a nonzero finite palm axis and finite ratio.
"""
import math

KIND = 'index_reach_xy_v1'
GRID_STEP_XY = 0.01
POINT_IDS = (0, 8, 9)
CHANNELS = ('thumb_curl', 'index_curl', 'middle_curl', 'ring_curl',
            'little_curl', 'thumb_spread_diagnostic')
COMMON_FLAGS = ('xy_projection_only', 'not_joint_angle', 'not_calibrated',
                'z_ignored', 'no_mapping', 'no_motor_targets')


def _ratio(points):
    tip = math.hypot(points[1][0] - points[0][0], points[1][1] - points[0][1])
    palm = math.hypot(points[2][0] - points[0][0], points[2][1] - points[0][1])
    if not math.isfinite(tip) or not math.isfinite(palm):
        return None, tip, palm, 'numeric_overflow'
    if palm == 0.0:
        return None, tip, palm, 'zero_palm_axis_xy'
    raw = tip / palm
    if not math.isfinite(raw):
        return None, tip, palm, 'ratio_numeric_overflow'
    return raw, tip, palm, 'numeric_xy_ratio_only'


def _finite_or_none(value):
    return value if value is not None and math.isfinite(value) else None


def measure_index_reach_xy(frame_or_values):
    """Pure fresh result; does not mutate input or validate unused XYZ.

    Source framing/full-63-finite validation belongs to the unchanged original
    parser. A direct call with NaN in an unused coordinate does not change this
    independent numeric ratio; the live wrapper still rejects invalid source.
    No age/freshness or original-R validity is inferred here.
    """
    values = frame_or_values.get('values') if isinstance(frame_or_values, dict) else frame_or_values
    result = {'kind': KIND, 'raw': None, 'valid': False,
              'quality_flags': list(COMMON_FLAGS), 'reason': 'invalid_value_count_or_container',
              'point_ids_zero_based': POINT_IDS, 'tip_distance_xy': None,
              'palm_axis_length_xy': None, 'observed_wire_grid_step_xy': GRID_STEP_XY,
              'palm_axis_grid_steps': None, 'tip_distance_grid_steps': None,
              'grid_step_max_abs_raw_change': None, 'grid_step_min_raw': None,
              'grid_step_max_raw': None, 'grid_step_finite_cases': 0,
              'grid_step_degenerate_cases': 0, 'grid_step_nonfinite_cases': 0,
              'grid_step_lost_coordinate_cases': 0,
              'grid_diagnostic_is_error_bound': False,
              'predicted_position': None, 'motor': None,
              'predicted_positions': dict.fromkeys(CHANNELS),
              'other_channels': dict.fromkeys(name for name in CHANNELS if name != 'index_curl'),
              'motion_authorized': False, 'normal_control_qualified': False}
    if not isinstance(values, (tuple, list)) or len(values) != 63:
        result['quality_flags'].append(result['reason'])
        return result
    points = []
    for index in POINT_IDS:
        point = []
        for value in values[3 * index:3 * index + 2]:
            if type(value) not in (int, float):
                result['reason'] = 'non_numeric_used_xy'
                result['quality_flags'].append(result['reason'])
                return result
            try:
                converted = float(value)
            except (ValueError, OverflowError):
                converted = float('nan')
            if not math.isfinite(converted):
                result['reason'] = 'non_finite_used_xy'
                result['quality_flags'].append(result['reason'])
                return result
            point.append(converted)
        points.append(point)
    raw, tip, palm, reason = _ratio(points)
    result.update(raw=raw, valid=raw is not None, reason=reason,
                  tip_distance_xy=_finite_or_none(tip), palm_axis_length_xy=_finite_or_none(palm))
    if raw is None:
        result['quality_flags'].append(reason)
        return result
    if tip == 0.0:
        result['quality_flags'].append('tip_wrist_coincident_xy_ratio_zero_not_angle')
    result['palm_axis_grid_steps'] = _finite_or_none(palm / GRID_STEP_XY)
    result['tip_distance_grid_steps'] = _finite_or_none(tip / GRID_STEP_XY)
    perturbed_values = []
    for point_index in range(3):
        for axis in range(2):
            for sign in (-1.0, 1.0):
                candidate = [list(point) for point in points]
                original = candidate[point_index][axis]
                candidate[point_index][axis] += sign * GRID_STEP_XY
                if candidate[point_index][axis] == original:
                    result['grid_step_lost_coordinate_cases'] += 1
                changed, _, _, changed_reason = _ratio(candidate)
                if changed is None:
                    key = 'grid_step_degenerate_cases' if changed_reason == 'zero_palm_axis_xy' else 'grid_step_nonfinite_cases'
                    result[key] += 1
                else:
                    perturbed_values.append(changed)
    result['grid_step_finite_cases'] = len(perturbed_values)
    if perturbed_values:
        result['grid_step_min_raw'] = min(perturbed_values)
        result['grid_step_max_raw'] = max(perturbed_values)
        result['grid_step_max_abs_raw_change'] = max(abs(value - raw) for value in perturbed_values)
    if result['grid_step_degenerate_cases'] or result['grid_step_nonfinite_cases']:
        result['quality_flags'].append('one_grid_step_has_undefined_ratio_cases')
    if result['grid_step_lost_coordinate_cases']:
        result['quality_flags'].append('grid_step_below_coordinate_float_resolution')
    return result
