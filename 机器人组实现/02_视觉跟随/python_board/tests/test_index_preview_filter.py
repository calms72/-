"""Pure timing/quality behavior, not a hardware stop or control test."""
import math
import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from index_preview_filter import IndexPreviewFilter

MS = 1000000


def make(**overrides):
    arguments = dict(median_window=3, ema_tau_s=.03, max_raw_rate=3,
                     absolute_tolerance=.02, max_gap_ns=150 * MS, max_age_ns=150 * MS)
    arguments.update(overrides)
    return IndexPreviewFilter(**arguments)


def seed(filter_, value=0):
    for time in (0, 10, 20):
        result = filter_.update(value, time * MS)
    return result


class FilterTimingTests(unittest.TestCase):
    def test_three_or_five_accepted_samples_required(self):
        for width in (3, 5):
            f = make(median_window=width)
            results = [f.update(.4, i * 30 * MS) for i in range(width)]
            self.assertTrue(all(not row['valid'] and row['filtered'] is None for row in results[:-1]))
            self.assertEqual(results[-1]['filtered'], .4)

    def test_ema_uses_irregular_actual_time_not_nominal_frames(self):
        f = make(max_raw_rate=100)
        seed(f)
        self.assertEqual(f.update(1, 30 * MS)['filtered'], 0)
        self.assertAlmostEqual(f.update(1, 50 * MS)['filtered'], 1 - math.exp(-20 / 30))
        self.assertAlmostEqual(f.update(1, 100 * MS)['filtered'], 1 - math.exp(-70 / 30))

    def test_rejected_points_never_refresh_accepted_deadline(self):
        f = make(max_raw_rate=.1, absolute_tolerance=0, max_age_ns=100 * MS)
        seed(f)
        for time in (30, 50, 70):
            row = f.update(1, time * MS)
            self.assertEqual(row['reason'], 'jump_rejected')
            self.assertEqual(row['last_accepted_ns'], 20 * MS)
            self.assertIsNone(row['filtered'])
        row = f.snapshot(119 * MS)
        self.assertFalse(row['valid'])
        self.assertEqual(row['reason'], 'warming_up')
        self.assertEqual(row['last_accepted_ns'], 20 * MS)
        self.assertEqual(row['accepted_age_ns'], 99 * MS)
        row = f.snapshot(120 * MS)
        self.assertFalse(row['valid'])
        self.assertEqual(row['reason'], 'accepted_expired')
        self.assertIsNone(row['last_accepted_ns'])
        self.assertEqual(row['jump_rejected_count'], 3)

    def test_gate_elapsed_time_since_last_accepted_not_rejected(self):
        f = make(max_raw_rate=1, absolute_tolerance=0)
        seed(f)
        self.assertEqual(f.update(.2, 30 * MS)['reason'], 'jump_rejected')
        # .09 is allowed after 100 ms since accepted t20, but would be rejected
        # if the rejected value or rejected timestamp had replaced the anchor.
        row = f.update(.09, 120 * MS)
        self.assertEqual(row['reason'], 'warming_up')
        self.assertEqual(row['last_accepted_ns'], 120 * MS)
        self.assertFalse(f.update(.095, 130 * MS)['valid'])
        self.assertTrue(f.update(.1, 140 * MS)['valid'])

    def test_invalid_and_nan_clear_old_values_and_require_new_warmup(self):
        for bad, valid in ((math.nan, True), (math.inf, True), (.5, False), ('0.5', True), (2, True)):
            f = make()
            seed(f, .4)
            row = f.update(bad, 30 * MS, valid)
            self.assertFalse(row['valid'])
            self.assertIsNone(row['filtered'])
            self.assertIsNone(row['last_accepted_ns'])
            results = [f.update(.8, time * MS) for time in (40, 50, 60)]
            self.assertEqual([row['valid'] for row in results], [False, False, True])
            self.assertEqual(results[-1]['filtered'], .8)

    def test_long_gap_reentry_discards_previous_filter(self):
        f = make()
        seed(f, .1)
        row = f.update(.9, 200 * MS)
        self.assertEqual(row['state_event'], 'frame_gap_reset')
        self.assertEqual(row['reason'], 'warming_up')
        self.assertIsNone(row['filtered'])
        self.assertFalse(f.update(.9, 230 * MS)['valid'])
        self.assertEqual(f.update(.9, 260 * MS)['filtered'], .9)

    def test_old_source_is_invalid_even_when_arriving_now(self):
        f = make()
        row = f.update(.5, 10 * MS, now_ns=160 * MS)
        self.assertEqual(row['reason'], 'source_stale')
        self.assertIsNone(row['last_accepted_ns'])
        f = make()
        seed(f)
        row = f.update(.5, 30 * MS, now_ns=200 * MS)
        self.assertEqual(row['reason'], 'source_stale')
        self.assertEqual(row['stale_count'], 1)
        self.assertEqual(row['reset_count'], 1)

    def test_duplicate_or_reversed_source_time_rejected_and_clears_state(self):
        for rx in (20 * MS, 19 * MS):
            f = make()
            seed(f, .4)
            row = f.update(.4, rx, now_ns=30 * MS)
            self.assertEqual(row['reason'], 'source_time_not_increasing')
            self.assertIsNone(row['filtered'])
            self.assertIsNone(row['last_accepted_ns'])
            self.assertEqual(f.update(.4, 40 * MS)['reason'], 'warming_up')

    def test_now_clock_reversal_future_source_and_bad_timestamp_fail_closed(self):
        f = make()
        seed(f)
        self.assertEqual(f.snapshot(19 * MS)['reason'], 'now_clock_invalid')
        self.assertIsNone(f.snapshot(30 * MS)['filtered'])
        for rx, now in ((40 * MS, 30 * MS), (True, 30 * MS), (-1, 30 * MS)):
            self.assertFalse(make().update(.5, rx, now_ns=now)['valid'])

    def test_explicit_reset_starts_new_quality_clock_session(self):
        f = make()
        seed(f, .4)
        self.assertEqual(f.reset()['reason'], 'manual_reset')
        self.assertEqual(f.update(.8, 0)['reason'], 'warming_up')
        self.assertEqual(f.update(.8, 10 * MS)['reason'], 'warming_up')
        row = f.update(.8, 20 * MS)
        self.assertTrue(row['valid'])
        self.assertFalse(row['motion_authorized'])
        self.assertTrue(row['feature_quality_only'])

    def test_explicit_configuration_rejects_bad_ranges(self):
        with self.assertRaises(ValueError):
            IndexPreviewFilter()
        for key, value in (('median_window', 1), ('median_window', True),
                           ('ema_tau_s', 0), ('ema_tau_s', math.nan),
                           ('max_raw_rate', 0), ('max_raw_rate', math.inf),
                           ('absolute_tolerance', -.1), ('max_gap_ns', 0),
                           ('max_age_ns', False)):
            with self.assertRaises(ValueError):
                make(**{key: value})
        with self.assertRaises(ValueError):
            make(max_raw_rate=1e308, max_age_ns=18446744073709551615)


if __name__ == '__main__':
    unittest.main()
