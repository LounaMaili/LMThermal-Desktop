"""Independent-reference metadata and spatial boundary checks for offline analysis."""

from pathlib import Path
import unittest

from physical_validation import analyze_frames, validate_plan

FIXTURE = Path(__file__).parent / 'fixtures' / 'radiometric-initial.raw'


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


if __name__ == '__main__':
    unittest.main()
