#!/usr/bin/env python3
"""Compare HT-301 transport words before and after one documented UVC control."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import struct
import subprocess

import numpy as np

from ht301_camera import discover_device, open_camera, read_raw_frame
from measurement_baseline import FRAME_WIDTH, IMAGE_HEIGHT, parse_frame


ZOOM_ABSOLUTE_CONTROL = 0x009A090D
THERMVIEWER_OUTPUT_ZERO = 32773
IMAGE_WORD_COUNT = FRAME_WIDTH * IMAGE_HEIGHT


def frame_metrics(raw: bytes) -> dict:
    """Summarize image words and confirmed trailer fields without Celsius math."""
    parsed = parse_frame(raw)
    words = np.frombuffer(raw, dtype="<u2", count=IMAGE_WORD_COUNT)
    y = parsed.image_y
    params = parsed.parameters
    top_bits = np.bincount(words >> 14, minlength=4)
    matrix = words.reshape(IMAGE_HEIGHT, FRAME_WIDTH)
    high_xy = struct.unpack_from('<2H', raw, 221188)
    low_xy = struct.unpack_from('<2H', raw, 221194)

    def word_at(xy):
        x, row = xy
        return int(matrix[row, x]) if x < FRAME_WIDTH and row < IMAGE_HEIGHT else None

    return {
        "image_word_min": int(words.min()),
        "image_word_max": int(words.max()),
        "image_words_at_most_0x3fff_percent": round(
            100.0 * np.count_nonzero(words <= 0x3FFF) / IMAGE_WORD_COUNT, 6
        ),
        "image_word_stddev": round(float(words.std()), 6),
        "image_word_unique_count": int(np.unique(words).size),
        "bit15_set_percent": round(float(np.count_nonzero(words & 0x8000) * 100 / IMAGE_WORD_COUNT), 6),
        "bit14_set_percent": round(float(np.count_nonzero(words & 0x4000) * 100 / IMAGE_WORD_COUNT), 6),
        "top_two_bits_percent": {
            f"{i:02b}": round(float(n * 100 / IMAGE_WORD_COUNT), 6)
            for i, n in enumerate(top_bits)
        },
        "masked_0x3fff_min_max_diagnostic_only": [int((words & 0x3fff).min()), int((words & 0x3fff).max())],
        "masked_0x7fff_min_max_diagnostic_only": [int((words & 0x7fff).min()), int((words & 0x7fff).max())],
        "image_y_min": int(y.min()),
        "image_y_max": int(y.max()),
        "image_y_stddev": round(float(y.std()), 4),
        "trailer_center_index": struct.unpack_from("<H", raw, 221208)[0],
        "trailer_spot0_index": struct.unpack_from("<H", raw, 221210)[0],
        "trailer_high_index": struct.unpack_from("<H", raw, 221192)[0],
        "trailer_low_index": struct.unpack_from("<H", raw, 221198)[0],
        "trailer_high_xy": list(high_xy),
        "trailer_low_xy": list(low_xy),
        "word_at_trailer_high_xy": word_at(high_xy),
        "word_at_trailer_low_xy": word_at(low_xy),
        "image_center_word_at_192_144": int(matrix[144, 192]),
        "center_8x8_word_min_max_mean": [int(matrix[140:148, 188:196].min()),
                                         int(matrix[140:148, 188:196].max()),
                                         float(matrix[140:148, 188:196].mean())],
        "lookup_base": struct.unpack_from("<H", raw, 223488)[0],
        "fpa_transform_input_at_221186": struct.unpack_from("<H", raw, 221186)[0],
        "calibration_temperature_word_at_223490": struct.unpack_from("<H", raw, 223490)[0],
        "calibration_coefficients": [
            struct.unpack_from("<f", raw, 223494 + 4 * i)[0] for i in range(5)
        ],
        "calibration_copy_matches": raw[223494:223514] == raw[224094:224114],
        "correction_setting": params.correction,
        "reflected_temp_setting": params.reflected_temp,
        "ambient_temp_setting": params.ambient_temp,
        "humidity_setting": params.humidity,
        "emissivity_setting": params.emissivity,
        "distance_setting": params.distance,
    }


def capture_metrics(capture, count: int) -> tuple[list[dict], list[bytes]]:
    """Read exact transport frames and retain bytes only for optional fixtures."""
    raw_frames = [read_raw_frame(capture) for _ in range(count)]
    return [frame_metrics(raw) for raw in raw_frames], raw_frames


def set_thermviewer_output_zero(device: Path) -> dict:
    """Send ThermViewer's HT-301 output-type-zero command through V4L2."""
    if shutil.which("v4l2-ctl") is None:
        raise RuntimeError("v4l2-ctl is required to apply the documented control")
    command = [
        "v4l2-ctl", "--device", str(device),
        f"--set-ctrl=zoom_absolute={THERMVIEWER_OUTPUT_ZERO}",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(
            f"V4L2 control failed ({result.returncode}): {result.stderr.strip()}"
        )
    return {
        "api": "V4L2 zoom_absolute via v4l2-ctl",
        "control_id": f"0x{ZOOM_ABSOLUTE_CONTROL:08x}",
        "value": THERMVIEWER_OUTPUT_ZERO,
        "source": "ThermViewer 2.0.23(ot) HT-301 output type 0",
    }


def run_diagnostic(
    capture,
    device: Path,
    count: int,
    warmup: int,
    settle: int,
    apply: bool,
    control=set_thermviewer_output_zero,
) -> tuple[dict, list[bytes]]:
    """Finish baseline capture before any optional, single control operation."""
    for _ in range(warmup):
        read_raw_frame(capture)
    before, _ = capture_metrics(capture, count)
    report = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "device": str(device),
        "image_rows": IMAGE_HEIGHT,
        "image_word_count_per_frame": IMAGE_WORD_COUNT,
        "before": before,
        "control": None,
        "settling_frames_discarded": 0,
        "after": None,
        "all_after_words_fit_14_bit_lookup": None,
        "interpretation": "Baseline only; no camera control was changed.",
    }
    if not apply:
        return report, []
    report["control"] = control(device)
    for _ in range(settle):
        read_raw_frame(capture)
    after, raw_after = capture_metrics(capture, count)
    fits = all(m["image_words_at_most_0x3fff_percent"] == 100.0 for m in after)
    report.update(
        settling_frames_discarded=settle,
        after=after,
        comparison={
            "before_word_min_range": [
                min(m["image_word_min"] for m in before),
                max(m["image_word_min"] for m in before),
            ],
            "after_word_min_range": [
                min(m["image_word_min"] for m in after),
                max(m["image_word_min"] for m in after),
            ],
            "before_word_max_range": [
                min(m["image_word_max"] for m in before),
                max(m["image_word_max"] for m in before),
            ],
            "after_word_max_range": [
                min(m["image_word_max"] for m in after),
                max(m["image_word_max"] for m in after),
            ],
            "before_14_bit_percent_range": [
                min(m["image_words_at_most_0x3fff_percent"] for m in before),
                max(m["image_words_at_most_0x3fff_percent"] for m in before),
            ],
            "after_14_bit_percent_range": [
                min(m["image_words_at_most_0x3fff_percent"] for m in after),
                max(m["image_words_at_most_0x3fff_percent"] for m in after),
            ],
            "trailer_summary_indices_changed": any(
                a[key] != b[key]
                for a, b in zip(after, before)
                for key in ("trailer_center_index", "trailer_high_index", "trailer_low_index")
            ),
        },
        all_after_words_fit_14_bit_lookup=fits,
        interpretation=(
            "All sampled image words fit the native 14-bit lookup; native output validation is still required."
            if fits else
            "The sampled image words still do not all fit the native 14-bit lookup."
        ),
    )
    return report, raw_after


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=Path, help="Override stable HT-301 discovery")
    parser.add_argument("--count", type=int, default=3, help="Frames per phase")
    parser.add_argument("--warmup", type=int, default=15, help="Baseline frames to discard")
    parser.add_argument("--settle", type=int, default=15, help="Frames to discard after control")
    parser.add_argument(
        "--apply-thermviewer-output0", action="store_true",
        help="Apply ThermViewer's HT-301 output-type-zero zoom control (32773)",
    )
    parser.add_argument(
        "--fixture-dir", type=Path,
        help="Save sanitized post-control frames only if every image word fits 14 bits",
    )
    args = parser.parse_args()
    if args.count < 1 or args.warmup < 0 or args.settle < 0:
        parser.error("count must be positive; warmup and settle must be nonnegative")
    if args.fixture_dir and not args.apply_thermviewer_output0:
        parser.error("--fixture-dir requires --apply-thermviewer-output0")

    device = args.device if args.device is not None else discover_device()
    capture = open_camera(device)
    try:
        report, raw_after = run_diagnostic(
            capture, device, args.count, args.warmup, args.settle,
            args.apply_thermviewer_output0,
        )
    finally:
        capture.release()

    if args.fixture_dir and report["all_after_words_fit_14_bit_lookup"]:
        from measurement_diagnostic import sanitize_fixture

        args.fixture_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for i, raw in enumerate(raw_after):
            path = args.fixture_dir / f"radiometric-{i:03d}.raw"
            path.write_bytes(sanitize_fixture(raw, seed=i + 1))
            paths.append(str(path))
        report["sanitized_fixtures"] = paths
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
