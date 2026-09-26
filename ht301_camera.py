"""HT-301 discovery and exact raw YUYV acquisition without UI dependencies."""

from pathlib import Path

import cv2
import numpy as np

from measurement_baseline import FRAME_BYTES, FRAME_HEIGHT, FRAME_WIDTH


class CameraError(RuntimeError):
    """The camera could not be discovered or captured in the expected format."""


def discover_device(
    by_id_dir: Path = Path("/dev/v4l/by-id"),
    sys_video_dir: Path = Path("/sys/class/video4linux"),
    dev_dir: Path = Path("/dev"),
) -> Path:
    """Prefer the stable Infiray index-0 symlink, then VID/PID sysfs identity."""
    for path in sorted(by_id_dir.glob("usb-Infiray_T3-317-13_*-video-index0")):
        if path.exists():
            return path

    for entry in sorted(sys_video_dir.glob("video*")):
        if not entry.name[5:].isdigit():
            continue
        try:
            index = (entry / "index").read_text().strip()
            interface = (entry / "device").resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if index != "0":
            continue
        for ancestor in (interface, *interface.parents):
            try:
                vid = (ancestor / "idVendor").read_text().strip().lower()
                pid = (ancestor / "idProduct").read_text().strip().lower()
            except OSError:
                continue
            if (vid, pid) == ("1514", "0001"):
                device = dev_dir / entry.name
                if device.exists():
                    return device
    raise CameraError("HT-301 video-index0 (USB 1514:0001) was not found")


def open_camera(device: Path | None = None) -> cv2.VideoCapture:
    """Open the selected V4L2 video stream with RGB conversion disabled."""
    selected = device if device is not None else discover_device()
    capture = cv2.VideoCapture(str(selected), cv2.CAP_V4L2)
    if not capture.isOpened():
        capture.release()
        raise CameraError(f"Cannot open HT-301 device {selected}")
    capture.set(cv2.CAP_PROP_CONVERT_RGB, 0)
    return capture


def read_raw_frame(capture: cv2.VideoCapture) -> bytes:
    """Read one complete unconverted 384 by 292 YUYV transport frame."""
    ok, frame = capture.read()
    if not ok or frame is None:
        raise CameraError("Frame read failed")
    if frame.shape != (FRAME_HEIGHT, FRAME_WIDTH, 2) or frame.dtype != np.uint8:
        raise CameraError(f"Unexpected camera frame shape or type: {frame.shape}, {frame.dtype}")
    raw = frame.tobytes()
    if len(raw) != FRAME_BYTES:
        raise CameraError(f"Expected {FRAME_BYTES} bytes, got {len(raw)}")
    return raw
