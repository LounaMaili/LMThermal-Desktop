#!/usr/bin/env python3
"""Inspect saved or live HT-301 transport frames without claiming thermometry."""

import argparse
import json
from pathlib import Path
import time

import numpy as np

from ht301_camera import open_camera, read_raw_frame
from measurement_baseline import (
    FRAME_WIDTH,
    IMAGE_BYTES,
    diagnostic_report,
    load_frame,
    parse_frame,
)


def sanitize_fixture(raw: bytes, seed: int = 1, scramble_image: bool = True) -> bytes:
    """Optionally scramble the scene and redact identifier-bearing trailer ranges.

    When requested, YUYV four-byte groups outside an 8 by 8 center patch are
    permuted, so
    brightness statistics and the real center patch survive without a
    recognizable portrait. The camera's two serial-bearing ranges are erased.
    These fixtures are for parser and arithmetic reproducibility, not spatial
    analysis or proving what the firmware's center field represents.
    """
    parse_frame(raw)
    result = bytearray(raw)
    if scramble_image:
        groups = np.frombuffer(result, dtype=np.uint8, count=IMAGE_BYTES).reshape(-1, 4)
        mask = np.ones((288, FRAME_WIDTH // 2), dtype=bool)
        mask[140:148, 94:98] = False
        selected = np.flatnonzero(mask.ravel())
        source = groups[selected].copy()
        rng = np.random.default_rng(seed)
        groups[selected] = source[rng.permutation(len(selected))]
    row_291 = 291 * FRAME_WIDTH * 2
    result[row_291 + 48:row_291 + 96] = bytes(48)
    result[row_291 + 510:row_291 + 550] = bytes(40)
    return bytes(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame", type=Path, action="append", help="Saved raw frame (repeatable)")
    parser.add_argument("--device", type=Path, help="Override automatic HT-301 discovery")
    parser.add_argument("--count", type=int, default=1, help="Live frames to inspect")
    parser.add_argument("--warmup", type=int, default=15, help="Frames to discard before capture")
    parser.add_argument("--interval", type=float, default=0.0, help="Seconds between captures")
    parser.add_argument("--save-dir", type=Path, help="Save privacy-scrubbed raw fixtures here")
    args = parser.parse_args()
    if args.count < 1 or args.warmup < 0 or args.interval < 0:
        parser.error("count must be positive; warmup and interval must be nonnegative")

    if args.frame:
        for path in args.frame:
            report = diagnostic_report(load_frame(path))
            print(json.dumps({"source": str(path), **report}, indent=2, allow_nan=False))
        return

    if args.save_dir:
        args.save_dir.mkdir(parents=True, exist_ok=True)
    capture = open_camera(args.device)
    try:
        for _ in range(args.warmup):
            read_raw_frame(capture)
        for i in range(args.count):
            raw = read_raw_frame(capture)
            if args.save_dir:
                path = args.save_dir / f"frame-{i:03d}.raw"
                path.write_bytes(sanitize_fixture(raw, seed=i + 1))
            else:
                path = None
            report = diagnostic_report(parse_frame(raw))
            print(json.dumps({"source": "live", "saved_fixture": str(path) if path else None, **report}, indent=2, allow_nan=False))
            if i + 1 < args.count:
                time.sleep(args.interval)
    finally:
        capture.release()


if __name__ == "__main__":
    main()
