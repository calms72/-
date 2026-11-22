"""Pure causal index feature-quality filter; only math, no I/O or targets.

The rate gate compares with the last ACCEPTED raw sample, not the last seen
packet. Jump rejections clear smoothing but retain that expiring rate anchor;
they never refresh its age. Invalid/old data, large frame gaps
or clock anomalies discard the window and EMA; fresh inputs must warm up again.
Quality becoming valid is not ARM/start permission or mechanical protection.
"""
import math


def _number(value, positive=False, nonnegative=False):
    if type(value) not in (int, float):
        raise ValueError('Explicit finite numeric configuration required')
    try:
        value = float(value)
    except (ValueError, OverflowError):
        raise ValueError('Explicit finite numeric configuration required')
    if (not math.isfinite(value) or positive and value <= 0 or
            nonnegative and value < 0):
        raise ValueError('Configuration outside finite allowed range')
    return value


def _timestamp(value):
    return type(value) is int and 0 <= value <= 18446744073709551615


class IndexPreviewFilter:
    def __init__(self, median_window=None, ema_tau_s=None, max_raw_rate=None,
                 absolute_tolerance=0.0, max_gap_ns=None, max_age_ns=None):
        if type(median_window) is not int or median_window not in (3, 5):
            raise ValueError('Explicit median window must be 3 or 5')
        if not _timestamp(max_gap_ns) or not max_gap_ns or not _timestamp(max_age_ns) or not max_age_ns:
            raise ValueError('Explicit positive gap and age deadlines required')
        self.median_window = median_window
        self.ema_tau_s = _number(ema_tau_s, positive=True)
        self.max_raw_rate = _number(max_raw_rate, positive=True)
        self.absolute_tolerance = _number(absolute_tolerance, nonnegative=True)
        self.max_gap_ns, self.max_age_ns = max_gap_ns, max_age_ns
        if not math.isfinite(self.absolute_tolerance + self.max_raw_rate * (max_age_ns / 1e9)):
            raise ValueError('Rate/age combination has nonfinite gate allowance')
        self.accepted_count = self.jump_rejected_count = self.bad_data_count = 0
        self.clock_rejected_count = self.stale_count = self.reset_count = 0
        self._last_seen_rx = self._last_now = None
        self._clear()

    def _clear(self):
        self._clear_smoothing()
        self._last_raw = self._last_accepted = None

    def _clear_smoothing(self):
        self._window = []
        self._filtered = self._filtered_rx = None

    def _discard(self):
        self._clear()
        self.reset_count += 1

    def reset(self):
        """Explicit new quality session; cumulative diagnostic counters remain."""
        self._discard()
        self._last_seen_rx = self._last_now = None
        return self._result(False, 'manual_reset')

    def _result(self, valid, reason, raw=None, now=None, event=None, threshold=None):
        age = now - self._last_accepted if now is not None and self._last_accepted is not None and now >= self._last_accepted else None
        return {'feature_quality_only': True, 'motion_authorized': False,
                'valid': valid, 'reason': reason, 'raw': raw,
                'filtered': self._filtered if valid else None,
                'last_accepted_ns': self._last_accepted, 'accepted_age_ns': age,
                'last_seen_rx_ns': self._last_seen_rx,
                'warmup_accepted_samples': len(self._window),
                'warmup_required': self.median_window, 'state_event': event,
                'gate_allowed_raw_delta': threshold,
                'accepted_count': self.accepted_count,
                'jump_rejected_count': self.jump_rejected_count,
                'bad_data_count': self.bad_data_count,
                'clock_rejected_count': self.clock_rejected_count,
                'stale_count': self.stale_count, 'reset_count': self.reset_count}

    def _now_ok(self, now):
        if not _timestamp(now) or self._last_now is not None and now < self._last_now:
            self.clock_rejected_count += 1
            self._discard()
            return False
        self._last_now = now
        return True

    def snapshot(self, now_ns):
        """Observe freshness during silence; no rejected point can renew it."""
        if not self._now_ok(now_ns):
            return self._result(False, 'now_clock_invalid')
        if self._last_accepted is None:
            return self._result(False, 'no_accepted_sample', now=now_ns)
        if now_ns - self._last_accepted >= self.max_age_ns:
            self.stale_count += 1
            self._discard()
            return self._result(False, 'accepted_expired', now=now_ns)
        ready = self._filtered is not None
        return self._result(ready, 'filtered' if ready else 'warming_up', self._last_raw, now_ns)

    def update(self, raw, rx_ns, valid=True, now_ns=None):
        """Process one source sample; now defaults to rx only for offline replay.

        A live caller must supply its current monotonic time and call snapshot
        during silence. Source receive time is not camera image freshness.
        """
        now = rx_ns if now_ns is None else now_ns
        if not self._now_ok(now):
            return self._result(False, 'now_clock_invalid')
        if not _timestamp(rx_ns) or rx_ns > now:
            self.clock_rejected_count += 1
            self._discard()
            return self._result(False, 'source_clock_invalid', now=now)
        if self._last_seen_rx is not None and rx_ns <= self._last_seen_rx:
            self.clock_rejected_count += 1
            self._discard()
            return self._result(False, 'source_time_not_increasing', now=now)
        previous_seen = self._last_seen_rx
        self._last_seen_rx = rx_ns
        # Reject an already-old incoming sample once, before the independent
        # checks for an otherwise-fresh reentry after an expired accepted value.
        if now - rx_ns >= self.max_age_ns:
            self.stale_count += 1
            self._discard()
            return self._result(False, 'source_stale', now=now)
        event = None
        if previous_seen is not None and rx_ns - previous_seen >= self.max_gap_ns:
            self._discard()
            event = 'frame_gap_reset'
        elif self._last_accepted is not None and now - self._last_accepted >= self.max_age_ns:
            self.stale_count += 1
            self._discard()
            event = 'accepted_age_reset'
        if valid is not True:
            self.bad_data_count += 1
            self._discard()
            return self._result(False, 'invalid_measurement', now=now, event=event)
        try:
            value = _number(raw)
        except ValueError:
            self.bad_data_count += 1
            self._discard()
            return self._result(False, 'invalid_raw', now=now, event=event)
        # The existing R feature has a mathematical 0..1 domain. This is a
        # feature-domain check, not a mechanical/protocol movement boundary.
        if value < 0 or value > 1:
            self.bad_data_count += 1
            self._discard()
            return self._result(False, 'raw_outside_feature_domain', value, now, event)
        threshold = None
        if self._last_accepted is not None:
            elapsed_s = (rx_ns - self._last_accepted) / 1e9
            threshold = self.absolute_tolerance + self.max_raw_rate * elapsed_s
            if abs(value - self._last_raw) > threshold:
                self.jump_rejected_count += 1
                self._clear_smoothing()
                self.reset_count += 1
                return self._result(False, 'jump_rejected', value, now, 'jump_smoothing_reset', threshold)
        self._last_raw, self._last_accepted = value, rx_ns
        self.accepted_count += 1
        self._window.append(value)
        self._window = self._window[-self.median_window:]
        if len(self._window) < self.median_window:
            return self._result(False, 'warming_up', value, now, event, threshold)
        median = sorted(self._window)[self.median_window // 2]
        if self._filtered is None:
            self._filtered = median
        else:
            alpha = -math.expm1(-(rx_ns - self._filtered_rx) / (self.ema_tau_s * 1e9))
            self._filtered += alpha * (median - self._filtered)
        self._filtered_rx = rx_ns
        return self._result(True, 'filtered', value, now, event, threshold)
