"""Check the capture boundary and fixed HT-301 control without hardware."""

from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from measurement_baseline import IMAGE_BYTES
from radiometric_mode_diagnostic import (
    THERMVIEWER_OUTPUT_ZERO,
    frame_metrics,
    run_diagnostic,
    set_thermviewer_output_zero,
)


FIXTURE = (Path(__file__).parent / "fixtures" / "scene-a.raw").read_bytes()


class FakeCapture:
    """Supply raw frames through the same read interface as the camera."""

    def __init__(self, frames):
        self.frames = iter(frames)


class RadiometricDiagnosticTests(unittest.TestCase):
    def test_saved_frame_and_synthetic_14_bit_words(self):
        baseline = frame_metrics(FIXTURE)
        self.assertEqual(baseline["image_words_at_most_0x3fff_percent"], 0.0)
        self.assertEqual(baseline["trailer_center_index"], 5165)
        self.assertEqual(baseline["trailer_high_index"], 5507)
        self.assertEqual(baseline["trailer_low_index"], 4918)
        self.assertTrue(baseline["calibration_copy_matches"])

        changed = bytearray(FIXTURE)
        changed[:IMAGE_BYTES] = struct.pack("<H", 5000) * (IMAGE_BYTES // 2)
        after = frame_metrics(bytes(changed))
        self.assertEqual(after["image_words_at_most_0x3fff_percent"], 100.0)
        self.assertEqual((after["image_word_min"], after["image_word_max"]), (5000, 5000))
        self.assertEqual(after["trailer_center_index"], baseline["trailer_center_index"])

    def test_baseline_precedes_the_only_control_and_settling_frames(self):
        frames = [FIXTURE] * 5
        capture = FakeCapture(frames)
        events = []

        def read(fake):
            events.append("read")
            return next(fake.frames)

        def control(device):
            events.append("control")
            return {"value": THERMVIEWER_OUTPUT_ZERO}

        with patch("radiometric_mode_diagnostic.read_raw_frame", side_effect=read):
            report, _ = run_diagnostic(capture, Path("/dev/example"), 1, 1, 2, True, control)
        self.assertEqual(events, ["read", "read", "control", "read", "read", "read"])
        self.assertEqual(report["settling_frames_discarded"], 2)
        self.assertFalse(report["all_after_words_fit_14_bit_lookup"])
        self.assertEqual(report["control"]["value"], 32773)

    def test_baseline_failure_cannot_apply_control(self):
        capture = FakeCapture([])
        controls = []
        with patch("radiometric_mode_diagnostic.read_raw_frame", side_effect=OSError("no frame")):
            with self.assertRaises(OSError):
                run_diagnostic(capture, Path("/dev/example"), 1, 0, 0, True, controls.append)
        self.assertEqual(controls, [])

    def test_control_uses_documented_zoom_value_only(self):
        with patch("radiometric_mode_diagnostic.shutil.which", return_value="/usr/bin/v4l2-ctl"):
            with patch("radiometric_mode_diagnostic.subprocess.run") as run:
                run.return_value.returncode = 0
                result = set_thermviewer_output_zero(Path("/dev/example"))
        command = run.call_args.args[0]
        self.assertEqual(command, [
            "v4l2-ctl", "--device", str(Path("/dev/example")), "--set-ctrl=zoom_absolute=32773"
        ])
        self.assertEqual(result["control_id"], "0x009a090d")


if __name__ == "__main__":
    unittest.main()
