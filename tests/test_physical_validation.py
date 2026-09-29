"""Independent-reference metadata and spatial boundary checks for offline analysis."""

from pathlib import Path
import unittest

from physical_validation import analyze_frame, analyze_frames, validate_plan

FIXTURE = Path(__file__).parent / 'fixtures' / 'radiometric-initial.raw'
HAND_FIXTURE = Path(__file__).parent / 'fixtures' / 'warm-hand-settled.raw'


class ValidationTests(unittest.TestCase):
    def test_regions_cannot_include_trailer_or_be_empty(self):
        for roi in ([0, 287, 384, 2], [383, 0, 2, 1], [0, 0, 0, 1], [-1, 0, 1, 1]):
            with self.assertRaises(ValueError):
                validate_plan({'targets': [{'name': 'target', 'roi_xywh': roi}]})

    def test_reference_requires_independent_measurement_metadata(self):
        target = {'name': 'target', 'roi_xywh': [190, 142, 4, 4],
                  'reference': {'surface_temperature_c': 20, 'uncertainty_c': .2}}
        with self.assertRaisesRegex(ValueError, 'instrument'):
            validate_plan({'targets': [target]})
        target['reference'].update(instrument='test probe', measured_at_utc='synthetic', method='unit-test only')
        self.assertEqual(len(validate_plan({'targets': [target]})), 1)

    def test_repeated_fixture_has_zero_drift_and_no_invented_reference(self):
        plan = {'targets': [{'name': 'center', 'roi_xywh': [190, 142, 4, 4], 'reference': None}]}
        report = analyze_frames([FIXTURE, FIXTURE], plan)
        self.assertEqual(report['region_stability']['center']['peak_to_peak_c'], 0)
        self.assertEqual(report['unique_image_count'], 1)
        self.assertEqual(report['longest_identical_image_run'], 2)
        self.assertNotIn('mean_minus_reference_c', report['frames'][0]['regions']['center'])
        self.assertTrue(report['frames'][0]['spatial_agreement']['high_index_equals_image_max'])

    def test_settled_hand_fixture_discriminates_background_and_retains_center_caution(self):
        raw = HAND_FIXTURE.read_bytes()
        self.assertEqual(raw[223536:223584], bytes(48))
        self.assertEqual(raw[223998:224038], bytes(40))
        regions = [
            {'name': 'central_hand', 'roi_xywh': [192, 128, 32, 32]},
            {'name': 'cool_background', 'roi_xywh': [16, 220, 32, 32]},
        ]
        result = analyze_frame(raw, regions)
        hand = result['regions']['central_hand']['lookup_min_max_mean_c'][2]
        cool = result['regions']['cool_background']['lookup_min_max_mean_c'][2]
        self.assertGreater(hand - cool, 8)
        self.assertEqual(result['metrics']['image_words_at_most_0x3fff_percent'], 100)
        self.assertTrue(result['spatial_agreement']['high_coordinate_matches_index'])
        self.assertTrue(result['spatial_agreement']['low_coordinate_matches_index'])
        self.assertFalse(result['spatial_agreement']['center_equals_pixel_192_144'])


if __name__ == '__main__':
    unittest.main()
