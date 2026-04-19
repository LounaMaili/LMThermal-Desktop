#!/usr/bin/env python3
"""
HT-301 Thermal Camera — Temperature Extraction Prototype
Phase 1: Read frames from /dev/video2, extract temperature data

Camera: Infiray T3-317-13 (HT-301)
Resolution: 384x292 @ 25fps, YUYV format
USB: VID 0x1514, PID 0x0001

Based on reverse engineering of HT-301ThermCameraViewerX V6.4 APK
and libthermometry.so native library.
"""

import cv2
import numpy as np
import math
import struct
import sys


# --- Frame structure constants ---
FRAME_WIDTH = 384
FRAME_HEIGHT = 292
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 2  # 224,256 (YUYV)
PARAMS_OFFSET = (FRAME_HEIGHT * 3 - 3) * 256 + 254  # 223,742
PARAMS_SIZE = 514
CENTER_TEMP_OFFSET = 356  # float32, offset within params


# --- libthermometry.so decoded functions ---

def get_temp_evn(a: float, env_temp: float, b: float) -> float:
    """
    Environment-corrected temperature from raw value.
    Decoded from libthermometry.so::GetTempEvn (0x850).

    Uses Stefan-Boltzmann law: radiated power ∝ T⁴

    Parameters:
        a: Raw sensor value (Y channel pixel, 0-255)
        env_temp: Environment/reflected temperature in °C
        b: Correction factor (emissivity × distance × gain)
    Returns:
        Temperature in °C
    """
    val = math.pow(a + 273.15, 4.0)
    val = val - env_temp
    val = b * val
    if val <= 0:
        return -273.15  # Invalid reading
    result = math.pow(val, 0.25)
    return result - 273.15


def init_temp_param(x: float, y: float) -> tuple:
    """
    Initialize calibration parameters.
    Decoded from libthermometry.so::InitTempParam (0x900).

    Parameters:
        x: Temperature range parameter
        y: Calibration coefficient
    Returns:
        (a, b) calibration parameters where a = y/(2x), b = (y/2x)²
    """
    a = y / (2.0 * x)
    b = (y * y) / (4.0 * x * x)
    return a, b


# --- Frame parameter extraction ---

def extract_params(raw_frame: bytes) -> dict:
    """
    Extract temperature parameters from the last 514 bytes of a YUYV frame.

    Returns dict with:
        env_temp1, env_temp2: Environment temperature (°C)
        emissivity: Surface emissivity (0-1)
        distance_factor: Distance correction factor
        gain: Auto-gain value
        center_temp: Pre-calculated center temperature (°C)
        offset_factor: Calibration offset
        calib_factor: Calibration factor
    """
    params = raw_frame[PARAMS_OFFSET:PARAMS_OFFSET + PARAMS_SIZE]

    def read_float32(offset):
        return struct.unpack_from('<f', params, offset)[0]

    return {
        'env_temp1': read_float32(4),
        'env_temp2': read_float32(8),
        'emissivity': read_float32(12),
        'distance_factor': read_float32(16),
        'active': struct.unpack_from('<I', params, 20)[0],
        'gain': read_float32(352),
        'center_temp': read_float32(CENTER_TEMP_OFFSET),
        'offset_factor': read_float32(364),
        'calib_factor': read_float32(368),
        'env_temp1_r': read_float32(376),
        'env_temp2_r': read_float32(380),
        'emissivity_r': read_float32(384),
        'distance_r': read_float32(388),
        'raw_bytes': params,
    }


def extract_y_channel(frame: np.ndarray) -> np.ndarray:
    """
    Extract Y (luminance) channel from YUYV frame.
    Y channel = thermal intensity (0-255).
    """
    return frame[:, :, 0]


# --- Temperature map computation ---

def compute_temp_map(y_channel: np.ndarray, params: dict) -> np.ndarray:
    """
    Compute per-pixel temperature map using GetTempEvn formula.

    Uses vectorized numpy operations for performance.
    The camera auto-adjusts gain/offset per scene, so the mapping
    Y → °C is scene-dependent.

    Parameters:
        y_channel: 2D array of Y values (0-255), shape (height, width)
        params: Extracted frame parameters
    Returns:
        2D array of temperatures in °C
    """
    env_temp = params['env_temp1']
    gain = params['gain'] if params['gain'] > 0 else 0.27

    # Estimate correction factor b from gain and emissivity
    # This is approximate — exact calibration requires CalcFixRaw()
    b = gain * params['emissivity']

    # Vectorized Stefan-Boltzmann calculation
    y = y_channel.astype(np.float64)
    val = np.power(y + 273.15, 4.0)
    val = val - env_temp
    val = b * val

    # Handle negative values (invalid readings)
    valid = val > 0
    result = np.full_like(y, -273.15)
    result[valid] = np.power(val[valid], 0.25) - 273.15

    return result


def compute_temp_map_empirical(y_channel: np.ndarray, params: dict) -> np.ndarray:
    """
    Simple empirical temperature mapping.
    Less accurate but faster. Coefficients derived from calibration
    against known temperatures (face ~35°C, wall ~20°C).

    Note: The camera auto-adjusts gain, so this is approximate.
    """
    y = y_channel.astype(np.float64)
    return 0.2143 * y - 3.14


# --- Main capture loop ---

def capture_single(device="/dev/video2"):
    """Capture a single frame and display temperature info."""
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    if not cap.isOpened():
        print(f"Error: Cannot open {device}")
        sys.exit(1)

    cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)

    # Skip warmup frames
    for _ in range(15):
        cap.read()

    ret, frame = cap.read()
    cap.release()

    if not ret:
        print("Error: Failed to capture frame")
        sys.exit(1)

    raw = frame.flatten().tobytes()
    params = extract_params(raw)
    y_ch = extract_y_channel(frame)

    print("=== Frame Parameters ===")
    print(f"  Environment temp:  {params['env_temp1']:.1f} °C")
    print(f"  Emissivity:        {params['emissivity']:.3f}")
    print(f"  Distance factor:   {params['distance_factor']:.3f}")
    print(f"  Gain:              {params['gain']:.4f}")
    print(f"  Center temp (pre): {params['center_temp']:.2f} °C")

    print(f"\n=== Y Channel Stats ===")
    print(f"  Shape: {y_ch.shape}")
    print(f"  Range: {y_ch.min()} - {y_ch.max()}")
    print(f"  Mean:  {y_ch.mean():.1f}")
    print(f"  Center (10x10): {y_ch[y_ch.shape[0]//2-5:y_ch.shape[0]//2+5, 192-5:192+5].mean():.1f}")

    # Compute temperature maps
    temp_sb = compute_temp_map(y_ch, params)
    center_y, center_x = y_ch.shape[0] // 2, y_ch.shape[1] // 2

    print(f"\n=== Temperature (Stefan-Boltzmann) ===")
    print(f"  Center:      {temp_sb[center_y, center_x]:.1f} °C")
    print(f"  Min:         {temp_sb[y_ch > 0].min():.1f} °C")
    print(f"  Max:         {temp_sb.max():.1f} °C")

    # Hot/cold spot
    hot_pos = np.unravel_index(temp_sb.argmax(), temp_sb.shape)
    cold_valid = temp_sb.copy()
    cold_valid[cold_valid < -200] = 999
    cold_pos = np.unravel_index(cold_valid.argmin(), cold_valid.shape)
    print(f"  Hottest:     {temp_sb[hot_pos]:.1f} °C at {hot_pos}")
    print(f"  Coldest:     {temp_sb[cold_pos]:.1f} °C at {cold_pos}")

    return frame, params, temp_sb


def capture_continuous(device="/dev/video2"):
    """
    Continuous capture with live temperature overlay.
    Press 'q' to quit, click to measure temperature at point.
    """
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    if not cap.isOpened():
        print(f"Error: Cannot open {device}")
        sys.exit(1)

    cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)

    # Skip warmup
    for _ in range(15):
        cap.read()

    click_point = [None]

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            click_point[0] = (x, y)

    cv2.namedWindow("HT-301 Thermal")
    cv2.setMouseCallback("HT-301 Thermal", on_mouse)

    print("Live view running. Click to measure temperature. Press 'q' to quit.")

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            raw = frame.flatten().tobytes()
            params = extract_params(raw)
            y_ch = extract_y_channel(frame)

            # Convert YUYV to BGR for display
            bgr = cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_YUYV)

            # Apply colormap (thermal look)
            y_norm = cv2.normalize(y_ch, None, 0, 255, cv2.NORM_MINMAX)
            colored = cv2.applyColorMap(y_norm, cv2.COLORMAP_JET)

            # Overlay center temperature
            center_temp = params['center_temp']
            cv2.putText(colored, f"Center: {center_temp:.1f} C",
                       (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            # Show params
            cv2.putText(colored, f"Env: {params['env_temp1']:.1f}C  Em: {params['emissivity']:.2f}",
                       (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

            # Click measurement
            if click_point[0] is not None:
                cx, cy = click_point[0]
                if 0 <= cx < y_ch.shape[1] and 0 <= cy < y_ch.shape[0]:
                    y_val = y_ch[cy, cx]
                    # Use center temp as reference to calibrate
                    # Simple ratio: if center_y gives center_temp
                    center_y_val = y_ch[y_ch.shape[0]//2, y_ch.shape[1]//2]
                    if center_y_val > 0:
                        # Approximate per-pixel temperature
                        ratio = center_temp / center_y_val if center_y_val > 0 else 0
                        click_temp = y_val * ratio
                    else:
                        click_temp = 0

                    cv2.circle(colored, (cx, cy), 5, (255, 255, 255), 1)
                    cv2.putText(colored, f"{click_temp:.1f} C",
                               (cx + 10, cy - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

            cv2.imshow("HT-301 Thermal", colored)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--live":
        capture_continuous()
    else:
        frame, params, temp = capture_single()
