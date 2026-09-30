"""Cadence, validity, tabular measurement and file lifecycle without hardware."""

import csv
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from celsius_palette import CelsiusRange, render_temperature
from measurement_logger import (COLUMNS, IncrementalLogWriter, LatestSampler,
                                MeasurementLogger, create_sample, session_metadata)
from radiometric_capture import load_capture
from radiometric_export import export_capture, snapshot_capture
from radiometric_session import FrameObservation, SessionState, inspect_frame, make_measurement
from roi_measurement import NativeROI, roi_statistics


HAND = (Path(__file__).parent / "fixtures/warm-hand-settled.raw").read_bytes()
UTC = "2026-09-30T12:00:00+00:00"


class MeasurementLoggerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.measurement = make_measurement(HAND, 0)
        cls.ready = FrameObservation(HAND, 0, SessionState.RADIOMETRIC_READY,
                                     inspect_frame(HAND), False, None, cls.measurement)

    def sample(self, observation=None, roi=None):
        return create_sample(self.ready if observation is None else observation, roi,
                             timestamp_utc=UTC, elapsed_s=1, sequence=1)

    def metadata(self):
        return session_metadata(created_at_utc=UTC, rate_hz=1, camera_identity="HT-301",
                                observation=self.ready)

    def test_valid_values_coordinates_distinct_centers_and_no_roi(self):
        row = self.sample()
        self.assertEqual(tuple(row), COLUMNS)
        self.assertTrue(row["measurement_valid"])
        self.assertEqual(row["high_c"], self.measurement.high_c)
        self.assertEqual((row["high_x_px"], row["high_y_px"]), self.measurement.high_xy)
        self.assertEqual(row["low_c"], self.measurement.low_c)
        self.assertEqual((row["low_x_px"], row["low_y_px"]), self.measurement.low_xy)
        self.assertEqual(row["literal_center_raw14"], int(self.measurement.raw14[144, 192]))
        self.assertEqual(row["literal_center_c"], float(self.measurement.temperature_c[144, 192]))
        self.assertNotEqual(row["literal_center_raw14"], row["trailer_center_raw14"])
        self.assertEqual(row["trailer_center_c"], self.measurement.trailer_center_c)
        self.assertTrue(all(value is None for key, value in row.items() if key.startswith("roi_")))

    def test_roi_statistics_are_matrix_slice_and_presentation_independent(self):
        roi = NativeROI(160, 180, 220, 240)
        before = self.sample(roi=roi)
        for palette, bounds in (("Inferno", (15, 45)), ("White hot", (25, 45))):
            render_temperature(self.measurement.temperature_c, *bounds, palette)
            self.assertEqual(self.sample(roi=roi), before)
        stats = roi_statistics(self.measurement.temperature_c, roi)
        self.assertEqual((before["roi_x1_px"], before["roi_y1_px"], before["roi_x2_px"], before["roi_y2_px"]),
                         (160, 180, 220, 240))
        self.assertEqual((before["roi_min_c"], before["roi_max_c"], before["roi_mean_c"], before["roi_pixel_count"]),
                         (stats.min_c, stats.max_c, stats.mean_c, 3600))

    def test_invalid_observations_are_gap_rows_even_with_stale_measurement(self):
        for state in (SessionState.DISPLAY_STREAM, SessionState.SHUTTER_TRANSIENT,
                      SessionState.RAW14_UNSETTLED, SessionState.ERROR):
            observation = replace(self.ready, state=state, rejection="held_image")
            row = self.sample(observation, NativeROI(10, 10, 20, 20))
            self.assertFalse(row["measurement_valid"])
            self.assertEqual(row["state"], state.value)
            self.assertEqual(row["status"], "held_image")
            self.assertEqual(row["roi_x1_px"], 10)
            for key in ("high_c", "low_c", "literal_center_c", "trailer_center_c", "roi_mean_c",
                        "roi_pixel_count", "high_x_px", "literal_center_raw14", "frame_measured_at_utc"):
                self.assertIsNone(row[key])
        for bad in (replace(self.ready, repeated_image=True), replace(self.ready, raw=b"wrong"),
                    replace(self.ready, measurement=replace(self.measurement, state=SessionState.RAW14_UNSETTLED))):
            self.assertFalse(self.sample(bad)["measurement_valid"])

    def test_cadence_latest_only_no_catchup_and_gap_recovery(self):
        sampler = LatestSampler(1, 10)
        sampler.update(self.ready, None)
        self.assertIsNone(sampler.sample_due(10.99, UTC))
        newest = replace(self.ready, measurement=replace(self.measurement, high_c=40))
        sampler.update(newest, None)
        row = sampler.sample_due(11, UTC)
        self.assertEqual(row["high_c"], 40)
        self.assertEqual(row["elapsed_s"], 1)
        self.assertEqual(row["sequence"], 1)
        self.assertIsNone(sampler.sample_due(11, UTC))
        held = sampler.sample_due(12, UTC)
        self.assertEqual(held["status"], "no_new_observation")
        self.assertFalse(held["measurement_valid"])
        sampler.update(replace(self.ready, state=SessionState.SHUTTER_TRANSIENT), None)
        self.assertFalse(sampler.sample_due(13, UTC)["measurement_valid"])
        sampler.update(replace(self.ready), None)
        late = sampler.sample_due(17.3, UTC)
        self.assertTrue(late["measurement_valid"])
        self.assertEqual(late["sequence"], 4)
        self.assertEqual(sampler.next_due, 18)
        self.assertIsNone(sampler.sample_due(17.9, UTC))

    def test_roi_move_and_clear_affect_only_later_rows(self):
        sampler = LatestSampler(2, 0)
        roi = NativeROI(10, 20, 30, 40)
        sampler.update(self.ready, roi)
        first = sampler.sample_due(.5, UTC)
        sampler.update(replace(self.ready), NativeROI(100, 120, 150, 160))
        second = sampler.sample_due(1, UTC)
        sampler.update(replace(self.ready), None)
        third = sampler.sample_due(1.5, UTC)
        self.assertEqual(first["roi_x1_px"], 10)
        self.assertEqual(second["roi_x1_px"], 100)
        self.assertIsNone(third["roi_mean_c"])
        self.assertIsNone(third["roi_x1_px"])
        self.assertEqual(first["roi_mean_c"], roi_statistics(self.measurement.temperature_c, roi).mean_c)

    def test_rates_and_no_observation_gap(self):
        for rate in (.5, 1, 2, 5, 10):
            sampler = LatestSampler(rate, 0)
            row = sampler.sample_due(1/rate, UTC)
            self.assertFalse(row["measurement_valid"])
            self.assertEqual(row["state"], "disconnected")
        with self.assertRaises(ValueError):
            LatestSampler(25, 0)

    def test_fractional_cadence_does_not_resample_same_deadline(self):
        for rate in (.5, 1, 2, 5, 10):
            sampler = LatestSampler(rate, 10)
            sampler.update(self.ready, None)
            deadline = 10 + 1/rate
            self.assertIsNotNone(sampler.sample_due(deadline, UTC))
            self.assertIsNone(sampler.sample_due(deadline, UTC))
            self.assertGreater(sampler.next_due, deadline)
            self.assertAlmostEqual(sampler.next_due, 10+2/rate)

    def test_csv_roundtrip_metadata_clean_stop_and_no_overwrite(self):
        with TemporaryDirectory() as root:
            path = Path(root) / "series.csv"
            writer = IncrementalLogWriter(path, self.metadata())
            self.assertFalse(path.exists())
            self.assertEqual(json.loads(writer.json_partial.read_text())["completion"], "incomplete")
            valid = self.sample(roi=NativeROI(10, 20, 30, 40))
            writer.append(valid)
            writer.append(self.sample(replace(self.ready, state=SessionState.RAW14_UNSETTLED)))
            # Incremental flushing makes rows available before final publication.
            self.assertEqual(len(writer.csv_partial.read_text().splitlines()), 3)
            writer.finish(stopped_at_utc=UTC, duration_s=2, reason="operator_stop")
            metadata = json.loads(path.with_suffix(".json").read_text())
            self.assertEqual((metadata["completion"], metadata["sample_count"], metadata["valid_sample_count"],
                              metadata["gap_sample_count"]), ("complete", 2, 1, 1))
            self.assertEqual(metadata["columns"], self.metadata()["columns"])
            self.assertEqual(metadata["parameters_at_start"]["emissivity"], self.measurement.parameters.emissivity)
            self.assertIn("absolute physical accuracy", metadata["accuracy_warning"])
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(float(rows[0]["roi_mean_c"]), valid["roi_mean_c"])
            self.assertEqual(rows[0]["measurement_valid"], "true")
            self.assertEqual(rows[1]["measurement_valid"], "false")
            self.assertEqual(rows[1]["high_c"], "")
            self.assertEqual(rows[1]["roi_mean_c"], "")
            self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ["series.csv", "series.json"])
            with self.assertRaises(FileExistsError):
                IncrementalLogWriter(path, self.metadata())
            with self.assertRaises(ValueError):
                writer.append(valid)

    def test_nonfinite_values_rejected_in_sample_and_metadata(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                self.sample(replace(self.ready, measurement=replace(self.measurement, high_c=bad)))
            with self.assertRaises(ValueError):
                session_metadata(created_at_utc=UTC, rate_hz=1, camera_identity=None,
                                 observation=replace(self.ready, measurement=replace(
                                     self.measurement, lookup_trace={"bad": bad})))

    def test_existing_sidecars_and_incomplete_files_protected(self):
        for extension in (".json", ".csv.incomplete", ".json.incomplete"):
            with TemporaryDirectory() as root:
                path = Path(root) / "series.csv"
                existing = Path(root) / ("series"+extension)
                existing.write_text("existing")
                with self.assertRaises(FileExistsError):
                    IncrementalLogWriter(path, self.metadata())
                self.assertEqual(existing.read_text(), "existing")
                self.assertEqual(len(list(Path(root).iterdir())), 1)

    def test_failure_keeps_identifiable_incomplete_pair_and_no_partial_final(self):
        with TemporaryDirectory() as root:
            writer = IncrementalLogWriter(Path(root)/"series.csv", self.metadata())
            writer.append(self.sample())
            with patch("measurement_logger.os.link", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    writer.finish(stopped_at_utc=UTC, duration_s=1, reason="operator_stop")
            self.assertTrue(writer.csv_partial.exists())
            self.assertEqual(json.loads(writer.json_partial.read_text())["completion"], "incomplete")
            self.assertFalse(writer.csv_path.exists())
            self.assertFalse(writer.json_path.exists())

    def test_json_publication_failure_rolls_back_only_new_final_csv(self):
        with TemporaryDirectory() as root:
            writer = IncrementalLogWriter(Path(root)/"series.csv", self.metadata())
            writer.append(self.sample())
            original_link = __import__("os").link
            calls = 0
            def fail_second(source, target):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("sidecar publication failure")
                return original_link(source, target)
            with patch("measurement_logger.os.link", side_effect=fail_second):
                with self.assertRaises(OSError):
                    writer.finish(stopped_at_utc=UTC, duration_s=1, reason="operator_stop")
            self.assertFalse(writer.csv_path.exists())
            self.assertFalse(writer.json_path.exists())
            self.assertTrue(writer.csv_partial.exists())
            self.assertEqual(json.loads(writer.json_partial.read_text())["completion"], "incomplete")

    def test_worker_failure_and_stop_produce_error_incomplete_metadata(self):
        with TemporaryDirectory() as root:
            # Patch cadence to emit a nonfinite row so real background failure handling runs.
            bad = self.sample()
            bad["high_c"] = float("nan")
            with patch.object(LatestSampler, "sample_due", return_value=bad):
                logger = MeasurementLogger(Path(root)/"bad.csv", rate_hz=10, observation=self.ready)
                logger._thread.join(2)
            self.assertEqual(logger.status.state, "error")
            with self.assertRaises(OSError):
                logger.stop()
            data = json.loads(logger.writer.json_partial.read_text())
            self.assertEqual(data["completion"], "incomplete")
            self.assertIn("Non-finite", data["error"])

    def test_threaded_logger_stops_flushes_and_is_independent_of_offline(self):
        with TemporaryDirectory() as root:
            export_capture(snapshot_capture(self.ready, "Inferno", CelsiusRange(15, 45), False),
                           Path(root)/"capture")
            offline = load_capture(Path(root)/"capture.json")
            with self.assertRaisesRegex(ValueError, "live"):
                MeasurementLogger(Path(root)/"offline.csv", observation=offline)
            self.assertFalse((Path(root)/"offline.csv.incomplete").exists())
            with self.assertRaises(ValueError):
                create_sample(offline, None, timestamp_utc=UTC, elapsed_s=1, sequence=1)
            logger = MeasurementLogger(Path(root)/"live.csv", observation=self.ready)
            status = logger.stop("window_closed")
            self.assertEqual(status.state, "complete")
            self.assertFalse(logger._thread.is_alive())
            metadata = json.loads(logger.writer.json_path.read_text())
            self.assertEqual(metadata["stop_reason"], "window_closed")
            self.assertEqual(metadata["sample_count"], 0)


if __name__ == "__main__":
    unittest.main()
