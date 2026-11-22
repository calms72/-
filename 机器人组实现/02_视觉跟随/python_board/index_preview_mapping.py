"""Pure index-only preview mapping. Only math is required on the PLC.

This module performs no file/device I/O and exposes no motion/configuration API.
Endpoints are explicit candidate numbers, not certified calibration or limits.
Finite out-of-window measurements retain an UNCLAMPED extrapolated prediction
and an explicit flag; no other channel is guessed. A caller must supply current
measurement validity; this module cannot establish camera freshness or stopping.
"""
import math

CHANNELS = ('thumb_curl', 'index_curl', 'middle_curl', 'ring_curl',
            'little_curl', 'thumb_spread_diagnostic')


def _finite_number(value):
    if type(value) not in (int, float):
        raise ValueError('Explicit finite numeric endpoint required')
    try:
        value = float(value)
    except (OverflowError, ValueError):
        raise ValueError('Explicit finite numeric endpoint required')
    if not math.isfinite(value):
        raise ValueError('Explicit finite numeric endpoint required')
    return value


class IndexPreviewMapping:
    def __init__(self, raw_straight=None, raw_gentle=None,
                 motor_straight=None, motor_gentle=None):
        self.raw_straight = _finite_number(raw_straight)
        self.raw_gentle = _finite_number(raw_gentle)
        self.motor_straight = _finite_number(motor_straight)
        self.motor_gentle = _finite_number(motor_gentle)
        self.raw_span = self.raw_gentle - self.raw_straight
        self.motor_span = self.motor_gentle - self.motor_straight
        if (not math.isfinite(self.raw_span) or self.raw_span <= 0 or
                not math.isfinite(self.motor_span) or self.motor_span <= 0):
            raise ValueError('Both endpoint pairs must be strictly increasing')
        if not math.isfinite(self.motor_span / self.raw_span):
            raise ValueError('Endpoint ratio is not finite')

    def predict(self, raw, valid=True):
        result = {'preview_only': True, 'motor_node': 3,
                  'motion_authorized': False, 'mechanical_limits_verified': False,
                  'raw': None, 'data_valid': False, 'predicted_position': None,
                  'raw_fraction': None, 'within_visual_preview_window': None,
                  'within_motor_preview_window': None, 'out_of_window': None,
                  'reason': 'invalid_measurement',
                  'positions': {name: None for name in CHANNELS}}
        if valid is not True:
            return result
        try:
            current = _finite_number(raw)
        except ValueError:
            result['reason'] = 'invalid_raw'
            return result
        result['raw'] = current
        fraction = (current - self.raw_straight) / self.raw_span
        predicted = self.motor_straight + fraction * self.motor_span
        if not math.isfinite(fraction) or not math.isfinite(predicted):
            result['reason'] = 'nonfinite_prediction'
            return result
        inside = self.raw_straight <= current <= self.raw_gentle
        motor_inside = self.motor_straight <= predicted <= self.motor_gentle
        result.update(data_valid=True, predicted_position=predicted,
                      raw_fraction=fraction, within_visual_preview_window=inside,
                      within_motor_preview_window=motor_inside,
                      out_of_window=not (inside and motor_inside),
                      reason='inside_preview_window' if inside and motor_inside else 'outside_preview_window')
        result['positions']['index_curl'] = predicted
        return result
