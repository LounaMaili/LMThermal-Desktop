"""Native ROI geometry and direct temperature-slice checks without hardware."""

from dataclasses import replace
from pathlib import Path
import unittest

import numpy as np

from mvp_presentation import native_edge_to_widget, native_to_widget
from radiometric_session import FrameObservation, SessionState, inspect_frame, make_measurement
from roi_measurement import (NativeROI, current_roi_statistics, roi_from_native_pixels,
                             roi_from_widget_drag, roi_statistics)


class ROIGeometryTests(unittest.TestCase):
    def test_widget_drag_and_reverse_direction_include_both_pixels(self):
        start = native_to_widget(10, 20, 800, 800)
        end = native_to_widget(30, 40, 800, 800)
        expected = NativeROI(10, 20, 31, 41)
        self.assertEqual(roi_from_widget_drag(start, end, 800, 800), expected)
        self.assertEqual(roi_from_widget_drag(end, start, 800, 800), expected)
        self.assertEqual(expected.pixel_count, 21 * 21)

    def test_edges_clip_and_letterbox_start_is_ignored(self):
        start = native_to_widget(10, 20, 800, 800)
        self.assertEqual(roi_from_widget_drag(start, (-100, -100), 800, 800),
                         NativeROI(0, 0, 11, 21))
        self.assertEqual(roi_from_widget_drag(start, (900, 900), 800, 800),
                         NativeROI(10, 20, 384, 288))
        self.assertIsNone(roi_from_widget_drag((10, 10), (200, 50), 800, 800))
        self.assertIsNone(roi_from_widget_drag((10, 10), start, 800, 800))
        self.assertEqual(roi_from_native_pixels((-5, -5), (900, 900)),
                         NativeROI(0, 0, 384, 288))

    def test_single_pixel_and_valid_half_open_bounds(self):
        point = native_to_widget(383, 287, 800, 600)
        roi = roi_from_widget_drag(point, point, 800, 600)
        self.assertEqual(roi, NativeROI(383, 287, 384, 288))
        self.assertEqual(roi.pixel_count, 1)
        for bounds in ((0, 0, 0, 1), (5, 5, 1, 1), (-1, 0, 1, 1),
                       (0, 0, 385, 288), (0.0, 0, 1, 1)):
            with self.assertRaises(ValueError):
                NativeROI(*bounds)

    def test_native_edges_reproject_after_resize(self):
        roi = NativeROI(10, 20, 31, 41)
        for width, height in ((800, 800), (700, 400), (384, 288)):
            first = native_to_widget(roi.x1, roi.y1, width, height)
            last = native_to_widget(roi.x2 - 1, roi.y2 - 1, width, height)
            self.assertEqual(roi_from_widget_drag(first, last, width, height), roi)
        self.assertEqual(native_edge_to_widget(0, 0, 800, 800), (0, 100))
        self.assertEqual(native_edge_to_widget(384, 288, 800, 800), (800, 700))


class ROIStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.matrix = np.arange(288 * 384, dtype=np.float32).reshape(288, 384) / 8

    def test_exact_slice_statistics_count_locations_and_no_mutation(self):
        roi = NativeROI(10, 20, 14, 23)
        before = self.matrix.copy()
        region = self.matrix[20:23, 10:14]
        stats = roi_statistics(self.matrix, roi)
        self.assertEqual(stats.pixel_count, 12)
        self.assertEqual(stats.min_c, float(region.min()))
        self.assertEqual(stats.max_c, float(region.max()))
        self.assertEqual(stats.mean_c, float(region.mean(dtype=np.float64)))
        self.assertEqual(stats.min_xy, (10, 20))
        self.assertEqual(stats.max_xy, (13, 22))
        np.testing.assert_array_equal(self.matrix, before)

    def test_single_pixel_and_ties_use_first_row_major_location(self):
        roi = NativeROI(10, 20, 11, 21)
        stats = roi_statistics(self.matrix, roi)
        self.assertEqual(stats.min_c, stats.max_c)
        self.assertEqual(stats.mean_c, float(self.matrix[20, 10]))
        self.assertEqual(stats.pixel_count, 1)
        self.matrix[20:24, 10:14] = 42
        stats = roi_statistics(self.matrix, NativeROI(10, 20, 14, 24))
        self.assertEqual(stats.min_xy, (10, 20))
        self.assertEqual(stats.max_xy, (10, 20))

    def test_undefined_values_and_wrong_shape_are_rejected(self):
        roi = NativeROI(0, 0, 10, 10)
        self.matrix[0, 0] = np.nan
        with self.assertRaises(ValueError):
            roi_statistics(self.matrix, roi)
        with self.assertRaises(ValueError):
            roi_statistics(np.zeros((292, 384), dtype=np.float32), roi)

    def test_only_current_ready_measurements_and_recovery(self):
        raw = (Path(__file__).parent / "fixtures/warm-hand-settled.raw").read_bytes()
        measured = make_measurement(raw, 0)
        ready = FrameObservation(raw, 0, SessionState.RADIOMETRIC_READY,
                                 inspect_frame(raw), False, None, measured)
        roi = NativeROI(160, 180, 220, 240)
        expected = roi_statistics(measured.temperature_c, roi)
        self.assertEqual(current_roi_statistics(ready, roi), expected)
        for state in SessionState:
            if state != SessionState.RADIOMETRIC_READY:
                self.assertIsNone(current_roi_statistics(replace(ready, state=state), roi))
        self.assertIsNone(current_roi_statistics(replace(ready, measurement=None), roi))
        self.assertIsNone(current_roi_statistics(ready, None))
        self.assertEqual(current_roi_statistics(ready, roi), expected)


if __name__ == "__main__":
    unittest.main()
