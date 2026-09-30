"""Saved measurement validation and rendering, independent of camera hardware."""

from dataclasses import FrozenInstanceError
import hashlib
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from celsius_palette import CelsiusRange, effective_range, render_temperature
from mvp_presentation import current_extrema, current_reading
from radiometric_capture import CaptureError, load_capture, save_rendered_image
from radiometric_export import export_capture, snapshot_capture
from radiometric_session import FrameObservation, SessionState, inspect_frame, make_measurement
from roi_measurement import NativeROI, current_roi_statistics, roi_statistics


HAND = (Path(__file__).parent / "fixtures/warm-hand-settled.raw").read_bytes()


class CaptureLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        measurement = make_measurement(HAND, 0)
        cls.ready = FrameObservation(HAND, 0, SessionState.RADIOMETRIC_READY,
                                     inspect_frame(HAND), False, None, measurement)

    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "capture.json"
        self.roi = NativeROI(160, 180, 220, 240)
        bounds = effective_range(self.ready.measurement.temperature_c, True, 15, 45)
        export_capture(snapshot_capture(self.ready, "Inferno", bounds, True, roi=self.roi),
                       self.path.with_suffix(""))

    def edit_json(self, change):
        data = json.loads(self.path.read_text())
        change(data)
        self.path.write_text(json.dumps(data))

    def edit_arrays(self, change):
        with np.load(self.path.with_suffix(".npz"), allow_pickle=False) as data:
            arrays = {key: data[key] for key in data.files}
        change(arrays)
        output = BytesIO()
        np.savez_compressed(output, **arrays)
        content = output.getvalue()
        self.path.with_suffix(".npz").write_bytes(content)
        self.edit_json(lambda data: data["files"].update(npz_sha256=hashlib.sha256(content).hexdigest()))

    def test_roundtrip_no_thermometry_immutable_data_and_metadata(self):
        with patch("radiometric_session.make_measurement", side_effect=AssertionError("recomputed")), \
                patch("native_equivalent_thermometry.build_lookup", side_effect=AssertionError("recomputed")):
            capture = load_capture(self.path)
        np.testing.assert_array_equal(capture.raw14, self.ready.measurement.raw14)
        np.testing.assert_array_equal(capture.temperature_c, self.ready.measurement.temperature_c)
        self.assertEqual(capture.raw_transport, HAND)
        self.assertEqual(capture.roi, self.roi)
        for matrix in (capture.raw14, capture.temperature_c):
            with self.assertRaises(ValueError):
                matrix[0, 0] = 0
            with self.assertRaises(ValueError):
                matrix.setflags(write=True)
        with self.assertRaises(FrozenInstanceError):
            capture.roi = None
        metadata = capture.metadata
        metadata["parameters"]["emissivity"] = 0
        self.assertGreater(capture.metadata["parameters"]["emissivity"], .9)
        self.assertEqual(current_roi_statistics(capture, self.roi),
                         roi_statistics(capture.temperature_c, self.roi))

    def test_cursor_extrema_and_distinct_centers_from_saved_values(self):
        capture = load_capture(self.path)
        reading = current_reading(capture, (192, 144))
        self.assertEqual(reading.raw14, capture.literal_center_index)
        self.assertEqual(reading.native_equivalent_c, capture.literal_center_c)
        self.assertNotEqual(capture.literal_center_index, capture.trailer_center_index)
        self.assertIsNone(current_reading(capture, None))
        self.assertIsNone(current_reading(capture, (384, 288)))
        self.assertEqual(current_extrema(capture), ((capture.high_xy, capture.high_c),
                                                   (capture.low_xy, capture.low_c)))

    def test_render_reproduces_original_then_new_palette_without_source_changes(self):
        capture = load_capture(self.path)
        originals = {ext: self.path.with_suffix(ext).read_bytes() for ext in (".json", ".npz", ".png")}
        target = self.path.parent / "new.png"
        save_rendered_image(capture, target, capture.original_palette, capture.original_bounds)
        np.testing.assert_array_equal(cv2.imread(str(target)),
                                      cv2.imread(str(self.path.with_suffix(".png"))))
        other = self.path.parent / "locked.png"
        save_rendered_image(capture, other, "White hot", CelsiusRange(25, 45))
        expected = render_temperature(capture.temperature_c, 25, 45, "White hot")
        np.testing.assert_array_equal(cv2.cvtColor(cv2.imread(str(other)), cv2.COLOR_BGR2RGB), expected)
        self.assertEqual({ext: self.path.with_suffix(ext).read_bytes() for ext in originals}, originals)
        self.assertFalse(other.with_suffix(".json").exists())
        self.assertFalse(other.with_suffix(".npz").exists())
        with self.assertRaises(FileExistsError):
            save_rendered_image(capture, other, "Turbo", CelsiusRange(25, 45))
        with self.assertRaisesRegex(ValueError, "original"):
            save_rendered_image(capture, self.path.with_suffix(".png"), "Turbo", CelsiusRange(25, 45))
        with self.assertRaises(ValueError):
            save_rendered_image(capture, self.path, "Turbo", CelsiusRange(25, 45))

    def test_locked_range_restored_exactly(self):
        self.edit_json(lambda data: data["presentation"].update(
            palette="White hot", range_mode="locked", effective_min_c=25, effective_max_c=45))
        capture = load_capture(self.path)
        self.assertFalse(capture.automatic_range)
        self.assertEqual(capture.original_bounds, CelsiusRange(25, 45))
        self.assertEqual(capture.original_palette, "White hot")

    def test_optional_roi_and_transport(self):
        self.edit_arrays(lambda arrays: arrays.pop("raw_transport"))
        def change(data):
            for key in ("roi", "raw_transport_sha256", "transport_bytes"):
                data.pop(key)
        self.edit_json(change)
        capture = load_capture(self.path)
        self.assertIsNone(capture.roi)
        self.assertIsNone(capture.raw_transport)
        np.testing.assert_array_equal(capture.temperature_c, self.ready.measurement.temperature_c)

    def test_missing_declared_transport_rejected(self):
        self.edit_arrays(lambda arrays: arrays.pop("raw_transport"))
        with self.assertRaisesRegex(CaptureError, "transport evidence"):
            load_capture(self.path)

    def test_missing_completion_or_companion_files(self):
        for extension in (".json", ".npz", ".png"):
            with self.subTest(extension=extension):
                path = self.path.with_suffix(extension)
                content = path.read_bytes()
                path.unlink()
                with self.assertRaises(CaptureError):
                    load_capture(self.path)
                path.write_bytes(content)
        self.edit_json(lambda data: data.pop("files"))
        with self.assertRaises(CaptureError):
            load_capture(self.path)

    def test_hash_corruption_of_npz_png_and_transport(self):
        for extension in (".npz", ".png"):
            with self.subTest(extension=extension):
                path = self.path.with_suffix(extension)
                content = path.read_bytes()
                path.write_bytes(content + b"corrupt")
                with self.assertRaisesRegex(CaptureError, "SHA-256 mismatch"):
                    load_capture(self.path)
                path.write_bytes(content)
        self.edit_json(lambda data: data.update(raw_transport_sha256="0" * 64))
        with self.assertRaisesRegex(CaptureError, "Transport SHA-256 mismatch"):
            load_capture(self.path)

    def test_wrong_array_shape_dtype_nonfinite_and_invalid_raw14(self):
        mutations = (
            lambda a: a.update(temperature_c=a["temperature_c"].astype(np.float64)),
            lambda a: a.update(raw14=a["raw14"][:287]),
            lambda a: a.update(raw14=a["raw14"].astype(">u2")),
            lambda a: a.update(temperature_c=a["temperature_c"].astype(object)),
            lambda a: a["temperature_c"].__setitem__((0, 0), np.nan),
            lambda a: a["raw14"].__setitem__((0, 0), 0x8000),
            lambda a: a.update(raw_transport=a["raw_transport"][:-1]),
        )
        original_json = self.path.read_bytes()
        original_npz = self.path.with_suffix(".npz").read_bytes()
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.path.write_bytes(original_json)
                self.path.with_suffix(".npz").write_bytes(original_npz)
                self.edit_arrays(mutation)
                with self.assertRaises(CaptureError):
                    load_capture(self.path)

    def test_raw_matrix_must_match_transport(self):
        self.edit_arrays(lambda a: a["raw14"].__setitem__((0, 0), 100))
        with self.assertRaisesRegex(CaptureError, "Transport image"):
            load_capture(self.path)

    def test_malformed_metadata_versions_ranges_coordinates_and_roi_statistics(self):
        mutations = (
            lambda d: d.update(version=2), lambda d: d.update(version=True),
            lambda d: d.update(format="other"), lambda d: d.update(native_height_px=292),
            lambda d: d.update(captured_at_utc="yesterday"),
            lambda d: d.update(frame_measured_at_utc="2026-09-30T10:00:00"),
            lambda d: d["parameters"].update(emissivity=float("nan")),
            lambda d: d["parameters"].update(distance="1"),
            lambda d: d["high"].update(x_px=384),
            lambda d: d["low"].update(native_equivalent_c=100),
            lambda d: d["literal_center_pixel"].update(x_px=191),
            lambda d: d["trailer_center"].update(raw14_index=0x4000),
            lambda d: d["presentation"].update(palette="unsupported"),
            lambda d: d["presentation"].update(range_mode="anything"),
            lambda d: d["presentation"].update(effective_min_c=999),
            lambda d: d["roi"]["geometry"].update(x2_px=500),
            lambda d: d["roi"]["statistics"].update(mean_c=0),
            lambda d: d["roi"]["statistics"].update(pixel_count=True),
            lambda d: d["roi"]["statistics"].update(min_xy_px=[0, 0]),
            lambda d: d.update(accuracy_warning=""),
            lambda d: d.update(lookup_trace=[]),
        )
        original = self.path.read_bytes()
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.path.write_bytes(original)
                self.edit_json(mutation)
                with self.assertRaises(CaptureError):
                    load_capture(self.path)

    def test_json_paths_are_not_followed_and_duplicate_keys_rejected(self):
        self.edit_json(lambda d: d["files"].update(npz="/etc/passwd", png="../../secret.png"))
        load_capture(self.path)
        text = self.path.read_text()
        self.path.write_text(text[:-1] + ', "version": 1}')
        with self.assertRaisesRegex(CaptureError, "Duplicate"):
            load_capture(self.path)

    def test_tolerance_accepts_rounding_but_not_material_roi_change(self):
        self.edit_json(lambda d: d["roi"]["statistics"].update(
            mean_c=d["roi"]["statistics"]["mean_c"] + 0.00001))
        load_capture(self.path)
        self.edit_json(lambda d: d["roi"]["statistics"].update(
            mean_c=d["roi"]["statistics"]["mean_c"] + .01))
        with self.assertRaisesRegex(CaptureError, "ROI mean_c"):
            load_capture(self.path)


if __name__ == "__main__":
    unittest.main()
