"""Native-coordinate and measurement-only desktop presentation regressions."""

from pathlib import Path
import unittest

from measurement_baseline import IMAGE_HEIGHT, FRAME_WIDTH
from mvp_presentation import (current_extrema, current_reading, display_image,
                              image_viewport, native_to_widget, widget_to_native)
from radiometric_session import FrameObservation, SessionState, inspect_frame, make_measurement


FIXTURES = Path(__file__).parent / "fixtures"
DISPLAY = (FIXTURES / "display-room-baseline.raw").read_bytes()
HAND = (FIXTURES / "warm-hand-settled.raw").read_bytes()


def observation(raw, state, measurement=None):
    """Build a session observation without opening camera hardware or Qt."""
    return FrameObservation(raw, 0.0, state, inspect_frame(raw), False, None, measurement)


class ViewportTests(unittest.TestCase):
    def test_native_mapping_at_two_sizes_and_letterbox(self):
        for size in ((768, 576), (1000, 600), (800, 800)):
            for point in ((0, 0), (192, 144), (383, 287)):
                widget = native_to_widget(*point, *size)
                self.assertEqual(widget_to_native(*widget, *size), point)
        area = image_viewport(800, 800)
        self.assertEqual((area.left, area.top, area.width, area.height),
                         (0.0, 100.0, 800.0, 600.0))
        self.assertIsNone(widget_to_native(400, 99, 800, 800))
        self.assertIsNone(widget_to_native(400, 700, 800, 800))
        self.assertIsNone(widget_to_native(-1, 300, 800, 800))
        self.assertIsNone(widget_to_native(800, 300, 800, 800))

    def test_outside_native_coordinate_rejected(self):
        with self.assertRaises(ValueError):
            native_to_widget(384, 100, 800, 600)
        with self.assertRaises(ValueError):
            image_viewport(0, 600)


class MeasurementPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.measured = make_measurement(HAND, 0.0)
        cls.ready = observation(HAND, SessionState.RADIOMETRIC_READY, cls.measured)

    def test_cursor_uses_native_matrix_and_full_raw14_word(self):
        reading = current_reading(self.ready, (191, 211))
        self.assertEqual(reading.raw14, int(self.measured.raw14[211, 191]))
        self.assertEqual(reading.native_equivalent_c, float(self.measured.temperature_c[211, 191]))
        self.assertIsNone(current_reading(self.ready, (384, 211)))

    def test_display_and_unsettled_frames_have_no_current_temperatures(self):
        display = observation(DISPLAY, SessionState.DISPLAY_STREAM)
        unsettled = observation(HAND, SessionState.RAW14_UNSETTLED)
        for frame in (display, unsettled):
            self.assertIsNone(current_reading(frame, (192, 144)))
            self.assertIsNone(current_extrema(frame))
        # Even a stale retained MeasurementFrame cannot override session state.
        stale = observation(HAND, SessionState.RAW14_UNSETTLED, self.measured)
        self.assertIsNone(current_reading(stale, (192, 144)))
        self.assertIsNone(current_extrema(stale))

    def test_extrema_and_two_centers_remain_distinct(self):
        extrema = current_extrema(self.ready)
        self.assertEqual(extrema[0], (self.measured.high_xy, self.measured.high_c))
        self.assertEqual(extrema[1], (self.measured.low_xy, self.measured.low_c))
        self.assertEqual((self.measured.trailer_center_index,
                          self.measured.literal_center_index), (5740, 5742))
        self.assertNotEqual(self.measured.trailer_center_index,
                            self.measured.parameters.calibration_1)

    def test_visualization_excludes_all_trailer_rows_and_does_not_change_raw(self):
        display = observation(DISPLAY, SessionState.DISPLAY_STREAM)
        before = self.measured.raw14.copy()
        for frame in (display, self.ready):
            image = display_image(frame)
            self.assertEqual(image.shape, (IMAGE_HEIGHT, FRAME_WIDTH))
        self.assertEqual(display_image(display)[144, 192], DISPLAY[2 * (144 * 384 + 192)])
        self.assertTrue((self.measured.raw14 == before).all())

    def test_viewer_has_no_legacy_device_or_thermometry_paths(self):
        source = (Path(__file__).parent.parent / "lmthermal_viewer.py").read_text()
        for forbidden in ("/dev/video2", "compute_temp_for_y", "get_temp_evn",
                          "extract_params", "center_temp", "COLORMAPS"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
