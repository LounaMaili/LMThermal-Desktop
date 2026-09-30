"""Capture serialization checks against a saved valid HT-301 hand frame."""

from dataclasses import replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from celsius_palette import CelsiusRange, render_temperature
from radiometric_export import (ACCURACY_WARNING, FORMAT_ID, FORMAT_VERSION,
                                export_capture, snapshot_capture)
from radiometric_session import FrameObservation, SessionState, inspect_frame, make_measurement
from roi_measurement import NativeROI, roi_statistics


HAND = (Path(__file__).parent / "fixtures/warm-hand-settled.raw").read_bytes()


class RadiometricExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.measurement = make_measurement(HAND, 0.0)
        cls.ready = FrameObservation(HAND, 0.0, SessionState.RADIOMETRIC_READY,
                                     inspect_frame(HAND), False, None, cls.measurement)

    def snapshot(self, palette="Inferno", bounds=CelsiusRange(15.0, 45.0), automatic=False):
        return snapshot_capture(self.ready, palette, bounds, automatic)

    def test_ready_only_and_array_validation(self):
        for state, measurement in ((SessionState.DISPLAY_STREAM, None),
                                   (SessionState.RAW14_UNSETTLED, None),
                                   (SessionState.SHUTTER_TRANSIENT, self.measurement)):
            observation = replace(self.ready, state=state, measurement=measurement)
            with self.assertRaisesRegex(ValueError, "radiometric-ready"):
                snapshot_capture(observation, "Inferno", CelsiusRange(15, 45), True)
        with self.assertRaisesRegex(ValueError, "same ready frame"):
            snapshot_capture(replace(self.ready, raw=b"other"), "Inferno", CelsiusRange(15, 45), True)
        with self.assertRaisesRegex(ValueError, "float32"):
            snapshot_capture(replace(self.ready, measurement=replace(
                self.measurement, temperature_c=self.measurement.temperature_c.astype(np.float64))),
                "Inferno", CelsiusRange(15, 45), True)

    def test_lossless_round_trip_png_metadata_and_immutable_snapshot(self):
        snapshot = self.snapshot()
        expected_rgb = render_temperature(self.measurement.temperature_c, 15, 45, "Inferno")
        # Simulate a later live frame/presentation update after the click.
        new_temperature = self.measurement.temperature_c.copy()
        new_temperature[:] = 99
        self.ready = replace(self.ready, measurement=replace(
            self.measurement, temperature_c=new_temperature))
        with TemporaryDirectory() as root:
            paths = export_capture(snapshot, Path(root) / "hand")
            with np.load(paths[".npz"], allow_pickle=False) as arrays:
                self.assertEqual(arrays["temperature_c"].dtype, np.dtype("float32"))
                self.assertEqual(arrays["raw14"].dtype, np.dtype("uint16"))
                self.assertEqual(arrays["temperature_c"].shape, (288, 384))
                self.assertEqual(arrays["raw14"].shape, (288, 384))
                np.testing.assert_array_equal(arrays["temperature_c"], self.measurement.temperature_c)
                np.testing.assert_array_equal(arrays["raw14"], self.measurement.raw14)
                self.assertEqual(arrays["raw_transport"].tobytes(), HAND)
            png_bgr = cv2.imread(str(paths[".png"]), cv2.IMREAD_COLOR)
            np.testing.assert_array_equal(cv2.cvtColor(png_bgr, cv2.COLOR_BGR2RGB), expected_rgb)
            data = json.loads(paths[".json"].read_text())
            self.assertEqual((data["format"], data["version"]), (FORMAT_ID, FORMAT_VERSION))
            self.assertNotIn("roi", data)
            self.assertEqual(data["presentation"], {
                "palette": "Inferno", "range_mode": "locked",
                "effective_min_c": 15.0, "effective_max_c": 45.0})
            self.assertEqual(data["high"]["raw14_index"], self.measurement.high_index)
            self.assertEqual((data["high"]["x_px"], data["high"]["y_px"]),
                             self.measurement.high_xy)
            self.assertEqual((data["low"]["x_px"], data["low"]["y_px"]),
                             self.measurement.low_xy)
            self.assertEqual(data["literal_center_pixel"]["raw14_index"],
                             self.measurement.literal_center_index)
            self.assertEqual(data["trailer_center"]["raw14_index"],
                             self.measurement.trailer_center_index)
            self.assertNotEqual(data["literal_center_pixel"]["raw14_index"],
                                data["trailer_center"]["raw14_index"])
            self.assertEqual(data["accuracy_warning"], ACCURACY_WARNING)
            self.assertNotIn("NaN", paths[".json"].read_text())
            self.assertNotIn("Infinity", paths[".json"].read_text())

    def test_roi_metadata_matches_same_captured_matrix_without_changing_npz(self):
        roi = NativeROI(160, 180, 220, 240)
        snapshot = snapshot_capture(self.ready, "Inferno", CelsiusRange(15, 45), True, roi=roi)
        with TemporaryDirectory() as root:
            paths = export_capture(snapshot, Path(root) / "roi")
            data = json.loads(paths[".json"].read_text())
            self.assertEqual(data["version"], 1)
            self.assertEqual(data["roi"]["coordinate_semantics"], "half_open")
            self.assertEqual(data["roi"]["geometry"], {
                "x1_px": 160, "y1_px": 180, "x2_px": 220, "y2_px": 240})
            with np.load(paths[".npz"], allow_pickle=False) as arrays:
                self.assertEqual(set(arrays.files), {"temperature_c", "raw14", "raw_transport"})
                np.testing.assert_array_equal(arrays["temperature_c"], self.measurement.temperature_c)
                self.assertEqual(data["roi"]["statistics"],
                                 roi_statistics(arrays["temperature_c"], roi).metadata())

    def test_nonfinite_metadata_rejected_and_existing_files_not_overwritten(self):
        bad = replace(self.ready, measurement=replace(
            self.measurement, lookup_trace={"broken": float("nan")}))
        with self.assertRaises(ValueError):
            snapshot_capture(bad, "Inferno", CelsiusRange(15, 45), True)
        with TemporaryDirectory() as root:
            base = Path(root) / "capture"
            existing = Path(str(base) + ".npz")
            existing.write_bytes(b"existing")
            with self.assertRaises(FileExistsError):
                export_capture(self.snapshot(), base)
            self.assertEqual(existing.read_bytes(), b"existing")
            self.assertEqual(sorted(p.name for p in Path(root).iterdir()), ["capture.npz"])

    def test_publish_failure_rolls_back_all_new_files(self):
        with TemporaryDirectory() as root:
            base = Path(root) / "capture"
            original_link = os.link
            calls = 0

            def fail_second(source, target):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated publish failure")
                return original_link(source, target)

            with patch("radiometric_export.os.link", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "simulated"):
                    export_capture(self.snapshot(), base)
            self.assertEqual(list(Path(root).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
