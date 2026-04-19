# LMThermal-Desktop

Desktop thermal camera viewer for the **Infiray HT-301 (T3-317-13)**.

Companion to [LMThermal](https://github.com/LounaMaili/LMThermal) which documents the hardware protocol and reverse engineering.

## Features (Phase 2 — in progress)

- Real-time thermal video with PyQt6
- Per-pixel temperature measurement (click & hover)
- Min/Max temperature markers
- 7 color palettes (JET, INFERNO, PLASMA, VIRIDIS, TURBO, WHITE HOT, BLACK HOT)
- Zoom (1x–4x)
- Frame info panel (env temp, emissivity, gain)
- Image capture with temperature overlay

## Requirements

```
pip install PyQt6 opencv-python numpy
```

## Usage

```bash
# Ensure camera is available
ls /dev/video2

# Run viewer (requires sudo for camera access)
sudo .venv/bin/python lmthermal_viewer.py

# Or run single-frame capture (no GUI)
sudo .venv/bin/python thermal_capture.py

# Live mode (basic OpenCV window)
sudo .venv/bin/python thermal_capture.py --live
```

## Camera Setup

The HT-301 is recognized as a standard UVC device on Linux. No special drivers needed.

```
VID: 0x1514 (Infiray)
PID: 0x0001
Device: /dev/video2 (YUYV 384×292 @ 25fps)
```

For access without sudo, add a udev rule:
```bash
echo 'SUBSYSTEM=="video4linux", ATTR{idVendor}=="1514", MODE="0666"' | sudo tee /etc/udev/rules.d/99-ht301.rules
sudo udevadm control --reload-rules
```

## Architecture

The temperature extraction is based on reverse engineering the official APK's `libthermometry.so`:

- Each frame (224,256 bytes YUYV) contains **514 bytes of temperature parameters** at the end
- Per-pixel temperature uses the **Stefan-Boltzmann law** (T⁴)
- Center temperature is pre-calculated by the camera firmware

See [LMThermal docs](https://github.com/LounaMaili/LMThermal) for full protocol documentation.

## License

To be determined.
