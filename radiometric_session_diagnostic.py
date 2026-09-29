#!/usr/bin/env python3
"""Run the supported HT-301 session, optionally with an operator live preview."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import threading
import time

from diagnostic_preview import DiagnosticPreview, PreviewClosed
from ht301_camera import discover_device, open_camera, read_raw_frame
from radiometric_sequence_diagnostic import ZoomControl
from radiometric_session import HT301RadiometricSession, SessionError, SessionState


WARNING = "Native-equivalent temperatures; absolute physical accuracy not yet independently validated."


class LatestFrameStream:
    """Capture continuously and expose the newest complete frame without backlog.

    The acquisition thread never transforms pixels. If rendering or lookup is
    slower than 25 fps, older frames are skipped rather than reported as live.
    """

    def __init__(self, capture):
        self.capture = capture
        self.condition = threading.Condition()
        self.latest = None
        self.sequence = 0
        self.consumed = 0
        self.error = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._capture, daemon=True)
        self.thread.start()

    def _capture(self):
        """Keep only the latest exact transport bytes and receipt timestamp."""
        try:
            while not self.stop.is_set():
                raw = read_raw_frame(self.capture)
                with self.condition:
                    self.latest = (time.monotonic(), raw)
                    self.sequence += 1
                    self.condition.notify_all()
        except Exception as exc:
            with self.condition:
                self.error = exc
                self.condition.notify_all()

    def next(self):
        """Wait for a newly captured frame, never an older queued image."""
        deadline = time.monotonic() + 4
        with self.condition:
            while self.sequence <= self.consumed and self.error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("No complete HT-301 frame received within four seconds")
                self.condition.wait(remaining)
            if self.error is not None:
                raise RuntimeError(f"HT-301 capture stopped: {self.error}") from self.error
            self.consumed = self.sequence
            return self.latest

    def close(self) -> bool:
        """Stop capture and report whether it is safe to release OpenCV."""
        self.stop.set()
        self.thread.join(timeout=5)
        return not self.thread.is_alive()


def parse_roi(value: str) -> tuple[int, int, int, int]:
    """Parse a repeated x,y,width,height operator overlay argument."""
    try:
        roi = tuple(int(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ROI must be x,y,width,height") from exc
    if len(roi) != 4:
        raise argparse.ArgumentTypeError("ROI must be x,y,width,height")
    return roi


def measurement_summary(frame) -> dict:
    """Report native-equivalent values and keep the two center notions apart."""
    params = asdict(frame.parameters)
    supported_fields = ("correction", "reflected_temp", "ambient_temp", "humidity",
                        "emissivity", "distance", "calibration_0", "calibration_1",
                        "calibration_2", "calibration_3", "calibration_4")
    return {
        "measured_at_utc": frame.measured_at_utc,
        "raw14_min_max": [frame.image_word_min, frame.image_word_max],
        "matrix_min_max_c": [float(frame.temperature_c.min()), float(frame.temperature_c.max())],
        "trailer_center": {"index": frame.trailer_center_index, "native_equivalent_c": frame.trailer_center_c},
        "literal_center_192_144": {"index": frame.literal_center_index, "native_equivalent_c": frame.literal_center_c},
        "high": {"index": frame.high_index, "native_equivalent_c": frame.high_c, "xy": frame.high_xy},
        "low": {"index": frame.low_index, "native_equivalent_c": frame.low_c, "xy": frame.low_xy},
        "parameters": {key: params[key] for key in supported_fields},
        "lookup_trace": frame.lookup_trace,
    }


def main() -> None:
    """Use i to initialize in preview; q/Escape closes the diagnostic window."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=Path, help="Override HT-301 discovery")
    parser.add_argument("--preview", action="store_true", help="Show display/raw14 view; press i to initialize")
    parser.add_argument("--roi", type=parse_roi, action="append", default=[], help="Overlay x,y,width,height; repeatable")
    parser.add_argument("--samples", type=int, default=5, help="Ready measurements to report without preview")
    parser.add_argument("--report", type=Path, help="Write a new JSON report; never overwrite")
    parser.add_argument("--shutter-discard", type=int, default=75)
    parser.add_argument("--live-frames", type=int, default=5)
    args = parser.parse_args()
    if args.samples < 1 or args.shutter_discard < 75 or args.live_frames < 2:
        parser.error("samples >=1, shutter-discard >=75 and live-frames >=2 are required")
    if args.report and args.report.exists():
        parser.error("Report already exists")

    device = args.device or discover_device()
    capture = open_camera(device)
    try:
        control = ZoomControl(device)
    except Exception:
        capture.release()
        raise
    preview = None
    stream = None
    report = {"device": str(device), "initial_zoom_absolute": None, "events": [],
              "rejected_frames": {}, "measurements": [], "warning": WARNING,
              "status": "running"}
    try:
        report["initial_zoom_absolute"] = control.get()
        if args.preview:
            preview = DiagnosticPreview(args.roi)
        stream = LatestFrameStream(capture)
        session = HT301RadiometricSession(stream, control,
                                         min_shutter_discard=args.shutter_discard,
                                         live_frames=args.live_frames,
                                         on_observation=preview.show if preview else None)
        if preview:
            print("Preview: aim in display mode, press i for the known official sequence; q/Escape closes.", flush=True)
            while True:
                observation = session.poll()
                if preview.last_key == ord("i"):
                    if observation.state != SessionState.DISPLAY_STREAM:
                        print("Initialization requires display frames; existing raw14 is read-only until its host state is known.", flush=True)
                    else:
                        session.initialize_normal_range()
                        print(WARNING, flush=True)
        else:
            first = session.initialize_normal_range()
            report["measurements"].append(measurement_summary(first))
            for _ in range(session.stage_limit):
                if len(report["measurements"]) >= args.samples:
                    break
                observation = session.poll()
                if observation.measurement is not None:
                    report["measurements"].append(measurement_summary(observation.measurement))
            if len(report["measurements"]) < args.samples:
                raise SessionError("Not enough live, valid measurement frames for requested samples")
            report["status"] = "complete"
        report["events"] = session.events
        report["rejected_frames"] = dict(session.rejections)
        report["time_to_ready_seconds"] = session.time_to_ready_seconds
        report["final_state"] = session.state.value
    except PreviewClosed:
        report["status"] = "operator_closed"
        report["events"] = session.events
        report["rejected_frames"] = dict(session.rejections)
        report["time_to_ready_seconds"] = session.time_to_ready_seconds
        report["final_state"] = session.state.value
        if preview and preview.latest and preview.latest.measurement:
            report["measurements"].append(measurement_summary(preview.latest.measurement))
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        if "session" in locals():
            report["events"] = session.events
            report["rejected_frames"] = dict(session.rejections)
            report["final_state"] = session.state.value
    finally:
        if preview:
            preview.close()
        can_release = stream is None or stream.close()
        control.close()
        if can_release:
            capture.release()
    if args.report:
        with args.report.open("x") as file:
            json.dump(report, file, indent=2, allow_nan=False)
            file.write("\n")
    print(json.dumps(report, allow_nan=False))
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
