#!/usr/bin/env python3
"""Native-equivalent HT-301 384-wide, range-120, lens-68 lookup reconstruction.

Reproduces the inspected x86_64 arithmetic with explicit float32 intermediates.
Agreement with APK instructions is not independent calibration validation. This
module is intentionally separate from GUI thermometry and never masks pixels.
"""

import math
import struct

import numpy as np

from measurement_baseline import IMAGE_BYTES, parse_frame

F = np.float32
KELVIN = F(273.15)


def calc_fix_raw(ambient, humidity, distance, emissivity, reflected):
    """Trace native CalcFixRaw, preserving scalar SSE rounding and double libm."""
    t, h, d, e, r = map(F, (ambient, humidity, distance, emissivity, reflected))
    polynomial = F(F(F(6.8455e-7) * t) * t) * t
    polynomial = polynomial + ((F(1.5587) + F(0.06939) * t) - (F(0.00027816) * t) * t)
    water = F(math.exp(float(polynomial)) * float(h))
    root_d, root_water = F(math.sqrt(float(d))), F(math.sqrt(float(water)))
    exponent1 = (root_water * F(-0.002276) + F(0.006569)) * -root_d
    exponent2 = (root_water * F(-0.006670) + F(0.012620)) * -root_d
    transmission = F(math.exp(float(exponent1)) * float(F(1.9))
                     + math.exp(float(exponent2)) * float(F(-0.9)))
    inverse = F(1) / (transmission * e)
    reflected_term = ((F(1) - e) * F(math.pow(float(r + KELVIN), 4.0))) * transmission
    radiation = F(math.pow(float(t + KELVIN), 4.0)) * (F(1) - transmission) + reflected_term
    return water, transmission, inverse, radiation


def build_lookup(raw: bytes) -> tuple[np.ndarray, dict]:
    """Build all 16384 entries for the official startup host state only.

    Native lens value 68 multiplies the stored uint16 distance by three. Its
    short-distance final correction is evaluated partly in double precision.
    Undefined low lookup entries remain NaN as in the native implementation.
    """
    parsed = parse_frame(raw)
    p = parsed.parameters
    c0, c1, c2, c3, c4 = map(F, struct.unpack_from('<5f', raw, 223494))
    settings = [p.ambient_temp, p.humidity, p.emissivity, p.reflected_temp, p.correction,
                c0, c1, c2, c3, c4]
    if (not all(math.isfinite(v) for v in settings) or c0 <= 0 or
            p.emissivity <= 0 or p.humidity < 0 or p.distance <= 0):
        raise ValueError('Unsupported nonfinite or invalid calibration/settings')
    if raw[223494:223514] != raw[224094:224114]:
        raise ValueError('Calibration coefficients disagree with parameter copies')
    fpa_word = struct.unpack_from('<H', raw, 221186)[0]
    calibration_word = struct.unpack_from('<H', raw, 223490)[0]
    fpa = F(20) - F(fpa_word - 7800) / F(36)
    calibration_temperature = F(calibration_word) / F(10) - KELVIN
    fix = max(0, int(F(390) - F(7.05) * fpa))
    if fix > 32767:
        raise ValueError('FPA transform outside the supported native GetFix branch')
    base = (struct.unpack_from('<H', raw, 223488)[0] - fix) & 0xffff
    init_a = c1 / (c0 + c0)
    init_b = (c1 * c1) / (c0 * (F(4) * c0))
    shifted = calibration_temperature + F(1.5)
    constant = (c0 * shifted) * shifted + shifted * c1
    linear = (fpa * c3 + (c2 * fpa) * fpa) + c4
    distance = F(3 * p.distance)
    water, transmission, inverse, radiation = calc_fix_raw(
        p.ambient_temp, p.humidity, distance, p.emissivity, p.reflected_temp)
    table = np.empty(16384, dtype=np.float32)
    for index in range(len(table)):
        radicand = ((F(index - base) * linear + constant) / c0) + init_b
        if radicand < 0:
            table[index] = np.nan
            continue
        calibrated = F(math.sqrt(float(radicand)) - float(init_a))
        powered = F(math.pow(float(calibrated + KELVIN), 4.0))
        corrected_power = inverse * (powered - radiation)
        if corrected_power < 0:
            table[index] = np.nan
            continue
        corrected = F(math.pow(float(corrected_power), .25)) - KELVIN
        # Lens 68 branch, x86_64 0x1d88 (distance < 60), otherwise 0x1af8.
        factor = float(distance * F(.85)) + 1.125 if distance < F(60) else 52.125
        table[index] = F(float(corrected) + float(corrected - F(p.ambient_temp)) * factor / 100.0)
    trace = {'host_range': 120, 'host_lens': 68, 'host_shutter_fix': 1.5,
             'fpa_word_at_221186': fpa_word, 'fpa_term': float(fpa),
             'calibration_word_at_223490': calibration_word,
             'calibration_temperature': float(calibration_temperature),
             'get_fix': fix, 'adjusted_lookup_base': base,
             'init_a': float(init_a), 'init_b': float(init_b),
             'linear': float(linear), 'constant': float(constant),
             'effective_native_distance': float(distance),
             'calc_fix_raw': list(map(float, (water, transmission, inverse, radiation))),
             'finite_lookup_entries': int(np.isfinite(table).sum())}
    return table, trace


def temperature_matrix(raw: bytes, lookup: np.ndarray | None = None) -> np.ndarray:
    """Map every 384 × 288 raw14 word to the native-equivalent Celsius LUT.

    The optional lookup must have been built from this frame's calibration
    inputs. Display words and undefined native entries are rejected rather
    than masked or converted into apparent measurements.
    """
    parsed = parse_frame(raw)
    words = np.frombuffer(raw, dtype='<u2', count=IMAGE_BYTES // 2).reshape(288, 384)
    if np.any(words >= 0x4000):
        raise ValueError('Full image words exceed native lookup range; masking is unsupported')
    table = build_lookup(raw)[0] if lookup is None else lookup
    if table.shape != (16384,) or table.dtype != np.float32:
        raise ValueError('Expected a 16384-entry float32 native lookup')
    matrix = table[words] + F(parsed.parameters.correction)
    if not np.isfinite(matrix).all():
        raise ValueError('Observed pixels select undefined native lookup entries')
    return matrix


def lookup_frame(raw: bytes) -> dict:
    """Report native-equivalent summary and matrix extrema, rejecting flag-bit inputs."""
    parsed = parse_frame(raw)
    words = np.frombuffer(raw, dtype='<u2', count=IMAGE_BYTES // 2)
    if np.any(words >= 0x4000):
        raise ValueError('Full image words exceed native lookup range; masking is unsupported')
    # Native search validates all six summary indices before writing its output.
    indices = [struct.unpack_from('<H', raw, offset)[0]
               for offset in (221208, 221192, 221198, 221210, 221212, 221200)]
    if any(i >= 0x4000 for i in indices):
        raise ValueError('Trailer summary index exceeds the native lookup range')
    table, trace = build_lookup(raw)
    correction = F(parsed.parameters.correction)
    summary = table[indices] + correction
    matrix = temperature_matrix(raw, table)
    if not np.isfinite(summary).all():
        raise ValueError('Observed summary indices select undefined native lookup entries')
    return {'status': 'Native-equivalent APK arithmetic; absolute accuracy unvalidated',
            'center_high_low_indices': indices[:3],
            'center_high_low': list(map(float, summary[:3])),
            'image_lookup_min_max': [float(matrix.min()), float(matrix.max())],
            'trace': trace}
