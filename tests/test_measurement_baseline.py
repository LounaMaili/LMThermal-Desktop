"""Reproducibility checks against privacy-scrubbed HT-301 captures."""

import hashlib
import math
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np

from ht301_camera import CameraError, discover_device
from measurement_baseline import (
    FRAME_BYTES,
    IMAGE_BYTES,
    PARAMS_OFFSET,
    PARAMS_SIZE,
    diagnostic_report,
    documented_calcfixraw_polynomial,
    get_temp_evn_trace,
    image_statistics,
    init_temp_param,
    parse_frame,
)
from measurement_diagnostic import sanitize_fixture


FIXTURES = Path(__file__).parent / "fixtures"
HASHES = {
    "scene-a.raw": "90006c5ec49e82b126cd961d074d38840013e654939522b9c18e977ca818353b",
    "scene-b.raw": "37578b11b89656ff3220f711721195f250613a7ab85086fa6f3d0b3243cfbb7d",
}


class RealFrameTests(unittest.TestCase):
    """Check transport, parsed fields and boundaries from saved hardware frames."""

    def test_exact_frame_size_and_reproducibility(self):
        for name, digest in HASHES.items():
            with self.subTest(name=name):
                raw = (FIXTURES / name).read_bytes()
                self.assertEqual(len(raw), FRAME_BYTES)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
                self.assertEqual(diagnostic_report(parse_frame(raw)), diagnostic_report(parse_frame(raw)))
                with self.assertRaises(ValueError):
                    parse_frame(raw[:-1])

    def test_parameter_offsets_and_decoding(self):
        raw = (FIXTURES / "scene-a.raw").read_bytes()
        frame = parse_frame(raw)
        p = frame.parameters
        self.assertEqual(PARAMS_OFFSET, 223742)
        self.assertEqual(PARAMS_SIZE, 514)
        self.assertEqual(len(frame.parameter_bytes), PARAMS_SIZE)
        self.assertEqual(frame.parameter_bytes, raw[PARAMS_OFFSET:])
        self.assertEqual(p.correction, 0.0)
        self.assertAlmostEqual(p.reflected_temp, 25.0)
        self.assertAlmostEqual(p.ambient_temp, 25.0)
        self.assertAlmostEqual(p.humidity, 0.45, places=6)
        self.assertAlmostEqual(p.emissivity, 0.98, places=6)
        self.assertEqual(p.distance, 1)
        self.assertAlmostEqual(p.calibration_0, 0.2705, places=6)
        self.assertAlmostEqual(p.calibration_1, 35.992, places=5)
        self.assertAlmostEqual(p.calibration_2, 0.00004, places=7)
        self.assertAlmostEqual(p.calibration_3, 0.0057, places=6)
        self.assertAlmostEqual(p.calibration_4, 0.8234, places=6)
        self.assertAlmostEqual(p.reflected_temp_repeat, p.reflected_temp)
        self.assertAlmostEqual(p.ambient_temp_repeat, p.ambient_temp)
        self.assertAlmostEqual(p.humidity_repeat, p.humidity)
        self.assertAlmostEqual(p.emissivity_repeat, p.emissivity)
        self.assertEqual(struct.unpack_from("<f", raw, PARAMS_OFFSET + 356)[0], p.calibration_1)

    def test_image_ends_before_four_nonimage_rows(self):
        raw = (FIXTURES / "scene-a.raw").read_bytes()
        frame = parse_frame(raw)
        self.assertEqual(IMAGE_BYTES, 221184)
        self.assertEqual(frame.image_y.shape, (288, 384))
        self.assertEqual(len(frame.nonimage_bytes), FRAME_BYTES - IMAGE_BYTES)
        self.assertEqual(PARAMS_OFFSET - IMAGE_BYTES, 2558)
        self.assertTrue(any(frame.nonimage_bytes[:2558]))
        full_y = np.frombuffer(raw, np.uint8).reshape(292, 384, 2)[:, :, 0]
        self.assertEqual(int(full_y.max()), 255)
        self.assertEqual(image_statistics(frame.image_y)["max_y"], 243)
        self.assertLess(image_statistics(frame.image_y)["max_position_yx"][0], 288)

    def test_fields_stay_constant_while_scene_changes(self):
        a = parse_frame((FIXTURES / "scene-a.raw").read_bytes())
        b = parse_frame((FIXTURES / "scene-b.raw").read_bytes())
        self.assertEqual(a.parameters.calibration_1, b.parameters.calibration_1)
        self.assertEqual(a.parameters.calibration_0, b.parameters.calibration_0)
        self.assertEqual(image_statistics(a.image_y)["transport_center_y"], 96)
        self.assertEqual(image_statistics(b.image_y)["transport_center_y"], 107)
        self.assertNotEqual(image_statistics(a.image_y)["mean_y"], image_statistics(b.image_y)["mean_y"])

    def test_fixture_has_no_recorded_camera_serial(self):
        for name in HASHES:
            with self.subTest(name=name):
                raw = (FIXTURES / name).read_bytes()
                row = 291 * 384 * 2
                self.assertEqual(raw[row + 48:row + 96], bytes(48))
                self.assertEqual(raw[row + 510:row + 550], bytes(40))

    def test_zeroed_parameter_frame_is_flagged(self):
        raw = bytearray((FIXTURES / "scene-a.raw").read_bytes())
        raw[PARAMS_OFFSET:] = bytes(PARAMS_SIZE)
        report = diagnostic_report(parse_frame(bytes(raw)))
        self.assertTrue(report["metadata_zeroed"])
        self.assertIsNone(report["legacy_rejected_hypothesis"])
        self.assertIsNone(report["calcfixraw_first_stage"])

    def test_native_lookup_inputs_expose_capture_mode(self):
        for name, center, high, low in (
            ("scene-a.raw", 5165, 5507, 4918),
            ("scene-b.raw", 5255, 5292, 5238),
        ):
            with self.subTest(name=name):
                report = diagnostic_report(parse_frame((FIXTURES / name).read_bytes()))
                lookup = report["native_lookup_inputs"]
                self.assertEqual(lookup["trailer_center_index"], center)
                self.assertEqual(lookup["trailer_high_index"], high)
                self.assertEqual(lookup["trailer_low_index"], low)
                self.assertTrue(lookup["calibration_copy_matches"])
                self.assertFalse(lookup["image_words_fit_14_bit_lookup"])
                self.assertGreater(lookup["image_word_min"], 0x3fff)

        compatible = bytearray((FIXTURES / "scene-a.raw").read_bytes())
        compatible[:IMAGE_BYTES] = struct.pack("<H", 5000) * (IMAGE_BYTES // 2)
        lookup = diagnostic_report(parse_frame(bytes(compatible)))["native_lookup_inputs"]
        self.assertTrue(lookup["image_words_fit_14_bit_lookup"])
        self.assertEqual((lookup["image_word_min"], lookup["image_word_max"]), (5000, 5000))


class ArithmeticTests(unittest.TestCase):
    """Pin documented arithmetic without claiming native measurement accuracy."""

    def test_legacy_center_trace_exposes_the_mismatch(self):
        frame = parse_frame((FIXTURES / "scene-a.raw").read_bytes())
        trace = diagnostic_report(frame)["legacy_rejected_hypothesis"]["trace"]
        self.assertEqual(trace["a"], 96)
        self.assertAlmostEqual(trace["b"], frame.parameters.calibration_0 * frame.parameters.humidity, places=6)
        self.assertAlmostEqual(trace["a_plus_273_15"], 369.15)
        self.assertAlmostEqual(trace["fourth_power"], 18569982353.117, places=2)
        self.assertAlmostEqual(trace["result_c_candidate"], -55.104154, places=5)
        self.assertLess(trace["result_c_candidate"], 0)

    def test_decoded_helpers_are_deterministic(self):
        self.assertEqual(init_temp_param(2.0, 8.0), (2.0, 4.0))
        with self.assertRaises(ValueError):
            init_temp_param(0.0, 8.0)
        trace = get_temp_evn_trace(20.0, 0.0, 1.0)
        self.assertAlmostEqual(trace["result_c_candidate"], 20.0)
        self.assertIsNone(get_temp_evn_trace(20.0, 0.0, -1.0)["result_c_candidate"])
        p = documented_calcfixraw_polynomial(25.0)
        expected = 1.5587 + 0.06939 * 25 - 0.00027816 * 25**2 + 6.8455e-7 * 25**3
        self.assertAlmostEqual(p["polynomial"], expected)
        self.assertAlmostEqual(p["exp_polynomial"], math.exp(expected))

    def test_scrubbing_preserves_thermal_statistics_and_center(self):
        raw = (FIXTURES / "scene-b.raw").read_bytes()
        scrubbed = sanitize_fixture(raw, seed=7)
        before = parse_frame(raw)
        after = parse_frame(scrubbed)
        self.assertEqual(before.parameters, after.parameters)
        self.assertEqual(image_statistics(before.image_y)["mean_y"], image_statistics(after.image_y)["mean_y"])
        self.assertEqual(image_statistics(before.image_y)["transport_center_y"], image_statistics(after.image_y)["transport_center_y"])
        self.assertFalse(np.array_equal(before.image_y, after.image_y))


class DiscoveryTests(unittest.TestCase):
    """Exercise stable and VID/PID discovery without connecting the camera."""

    def test_by_id_preferred_over_sysfs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            by_id = root / "by-id"
            dev = root / "dev"
            by_id.mkdir()
            dev.mkdir()
            (dev / "video2").touch()
            link = by_id / "usb-Infiray_T3-317-13_sample-video-index0"
            link.symlink_to(dev / "video2")
            found = discover_device(by_id, root / "missing", dev)
            self.assertEqual(found, link)

    def test_vid_pid_fallback_requires_index_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sysdir = root / "sys"
            dev = root / "dev"
            camera = root / "camera"
            interface = camera / "interface"
            interface.mkdir(parents=True)
            sysdir.mkdir()
            dev.mkdir()
            (camera / "idVendor").write_text("1514")
            (camera / "idProduct").write_text("0001")
            for index in (0, 1):
                entry = sysdir / f"video{index}"
                entry.mkdir()
                (entry / "index").write_text(str(index))
                (entry / "device").symlink_to(interface)
                (dev / entry.name).touch()
            self.assertEqual(discover_device(root / "missing", sysdir, dev), dev / "video0")
            (camera / "idProduct").write_text("9999")
            with self.assertRaises(CameraError):
                discover_device(root / "missing", sysdir, dev)


if __name__ == "__main__":
    unittest.main()
