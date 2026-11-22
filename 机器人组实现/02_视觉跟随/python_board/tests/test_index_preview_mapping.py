"""Pure mapping tests, never device I/O or motor control."""
import math
import pathlib
import sys
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from index_preview_mapping import IndexPreviewMapping, CHANNELS


class IndexMappingTests(unittest.TestCase):
    def test_explicit_endpoints_and_midpoint(self):
        mapping = IndexPreviewMapping(.1, .6, 10, 110)
        for raw, position in ((.1, 10), (.35, 60), (.6, 110)):
            result = mapping.predict(raw)
            self.assertAlmostEqual(result['predicted_position'], position)
            self.assertTrue(result['within_visual_preview_window'])
            self.assertTrue(result['within_motor_preview_window'])
            self.assertFalse(result['out_of_window'])
            self.assertTrue(result['preview_only'])
            self.assertFalse(result['motion_authorized'])

    def test_outside_window_retains_unclamped_extrapolation(self):
        mapping = IndexPreviewMapping(.1, .6, 0, 100)
        for raw, expected in ((0, -20), (.85, 150)):
            result = mapping.predict(raw)
            self.assertAlmostEqual(result['predicted_position'], expected)
            self.assertTrue(result['out_of_window'])
            self.assertFalse(result['within_motor_preview_window'])
            self.assertEqual(result['reason'], 'outside_preview_window')

    def test_other_five_channels_never_guessed_and_results_do_not_alias(self):
        mapping = IndexPreviewMapping(0, 1, 0, 128)
        a, b = mapping.predict(.25), mapping.predict(.5)
        self.assertEqual(a['positions']['index_curl'], 32)
        self.assertEqual(b['positions']['index_curl'], 64)
        for name in CHANNELS:
            if name != 'index_curl':
                self.assertIsNone(a['positions'][name])
        a['positions']['thumb_curl'] = 999
        self.assertIsNone(mapping.predict(.25)['positions']['thumb_curl'])

    def test_missing_endpoint_rejected(self):
        with self.assertRaises(ValueError):
            IndexPreviewMapping()
        for i in range(4):
            values = [0, 1, 0, 128]
            values[i] = None
            with self.assertRaises(ValueError):
                IndexPreviewMapping(*values)

    def test_nonfinite_nonnumeric_bool_endpoints_rejected(self):
        for bad in (math.nan, math.inf, -math.inf, '1', True, [], 10 ** 400):
            for i in range(4):
                values = [0, 1, 0, 128]
                values[i] = bad
                with self.assertRaises(ValueError):
                    IndexPreviewMapping(*values)

    def test_duplicate_or_descending_endpoints_rejected(self):
        for values in ((1, 1, 0, 128), (2, 1, 0, 128),
                       (0, 1, 128, 128), (0, 1, 128, 0)):
            with self.assertRaises(ValueError):
                IndexPreviewMapping(*values)

    def test_unrepresentable_span_or_ratio_rejected(self):
        for values in ((-1e308, 1e308, 0, 128),
                       (0, 1, -1e308, 1e308), (0, 1e-320, 0, 128)):
            with self.assertRaises(ValueError):
                IndexPreviewMapping(*values)

    def test_invalid_measurement_and_raw_never_yield_position(self):
        mapping = IndexPreviewMapping(0, 1, 0, 128)
        for raw, valid in ((.5, False), (.5, 1), (.5, None),
                           (None, True), (math.nan, True), (math.inf, True), ('0.5', True)):
            result = mapping.predict(raw, valid)
            self.assertIsNone(result['predicted_position'])
            self.assertFalse(result['data_valid'])
            self.assertTrue(all(value is None for value in result['positions'].values()))

    def test_prediction_overflow_rejected_without_clamping(self):
        mapping = IndexPreviewMapping(0, 1, 0, 1e308)
        result = mapping.predict(2)
        self.assertEqual(result['reason'], 'nonfinite_prediction')
        self.assertIsNone(result['predicted_position'])


if __name__ == '__main__':
    unittest.main()
