"""Regression against output of the hash-pinned official native APK library."""

from pathlib import Path
import unittest

import numpy as np

from experimental_thermometry import build_lookup, lookup_frame, temperature_matrix

FIXTURES = Path(__file__).parent / 'fixtures'


class LookupTests(unittest.TestCase):
    def test_all_16384_entries_match_executed_native_reference(self):
        for path in sorted(FIXTURES.glob('radiometric-*.raw')):
            with self.subTest(fixture=path.name):
                expected = np.load(path.with_name(path.stem + '-native-lut.npy'), allow_pickle=False)
                actual, trace = build_lookup(path.read_bytes())
                self.assertEqual(actual.shape, (16384,))
                self.assertEqual(actual.dtype, np.float32)
                np.testing.assert_array_equal(np.isnan(actual), np.isnan(expected))
                np.testing.assert_allclose(actual, expected, rtol=0, atol=0.0001, equal_nan=True)
                self.assertEqual(trace['effective_native_distance'], 3.0)

    def test_corrected_trailer_inputs_and_native_summary(self):
        report = lookup_frame((FIXTURES / 'radiometric-initial.raw').read_bytes())
        self.assertEqual(report['trace']['fpa_word_at_221186'], 7180)
        self.assertEqual(report['trace']['calibration_word_at_223490'], 3105)
        self.assertAlmostEqual(report['trace']['fpa_term'], 37.22222137)
        self.assertAlmostEqual(report['trace']['calibration_temperature'], 37.35000610)
        np.testing.assert_allclose(report['center_high_low'],
                                   [16.282669067382812, 16.800127029418945, 12.868019104003906],
                                   rtol=0, atol=.0001)

    def test_new_spatial_fixtures_keep_trailer_extrema_and_remove_identifiers(self):
        from radiometric_mode_diagnostic import frame_metrics
        for path in sorted(FIXTURES.glob('*room*.raw')):
            raw = path.read_bytes()
            self.assertEqual(len(raw), 224256)
            self.assertEqual(raw[223536:223584], bytes(48))
            self.assertEqual(raw[223998:224038], bytes(40))
            metrics = frame_metrics(raw)
            self.assertTrue(metrics['calibration_copy_matches'])
            if path.name.startswith('radiometric'):
                self.assertEqual(metrics['image_words_at_most_0x3fff_percent'], 100)
                self.assertEqual(metrics['trailer_high_index'], metrics['image_word_max'])
                self.assertEqual(metrics['trailer_low_index'], metrics['image_word_min'])
                self.assertEqual(metrics['word_at_trailer_high_xy'], metrics['trailer_high_index'])
                self.assertEqual(metrics['word_at_trailer_low_xy'], metrics['trailer_low_index'])

    def test_display_words_are_rejected_without_masking(self):
        with self.assertRaisesRegex(ValueError, 'masking is unsupported'):
            lookup_frame((FIXTURES / 'scene-a.raw').read_bytes())
        with self.assertRaisesRegex(ValueError, 'masking is unsupported'):
            temperature_matrix((FIXTURES / 'scene-a.raw').read_bytes())

    def test_full_temperature_matrix_uses_native_lookup_and_excludes_trailer(self):
        raw = (FIXTURES / 'radiometric-initial.raw').read_bytes()
        lookup, _ = build_lookup(raw)
        matrix = temperature_matrix(raw, lookup)
        self.assertEqual(matrix.shape, (288, 384))
        self.assertEqual(matrix.dtype, np.float32)
        self.assertTrue(np.isfinite(matrix).all())
        words = np.frombuffer(raw, dtype='<u2', count=384 * 288).reshape(288, 384)
        self.assertEqual(matrix[144, 192], lookup[words[144, 192]])
        summary = lookup_frame(raw)
        self.assertEqual([float(matrix.min()), float(matrix.max())], summary['image_lookup_min_max'])

    def test_invalid_trailer_indices_and_emissivity_are_rejected(self):
        import struct
        raw = bytearray((FIXTURES / 'radiometric-initial.raw').read_bytes())
        struct.pack_into('<H', raw, 221208, 0x8000)
        with self.assertRaisesRegex(ValueError, 'Trailer summary'):
            lookup_frame(raw)
        struct.pack_into('<H', raw, 221208, 4844)
        struct.pack_into('<f', raw, 223758, 0)
        with self.assertRaisesRegex(ValueError, 'invalid calibration'):
            build_lookup(raw)


if __name__ == '__main__':
    unittest.main()
