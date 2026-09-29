#!/usr/bin/env python3
"""Offline region/stability comparison for controlled HT-301 target captures.

No controls, fitting, or GUI integration. Optional independently measured
surface temperatures are compared to the reconstructed lookup, not used to
adjust it. Keep an unshuffled, identifier-redacted frame for spatial checks.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from experimental_thermometry import build_lookup, lookup_frame, temperature_matrix
from radiometric_mode_diagnostic import frame_metrics


def validate_plan(plan):
    """Reject ambiguous regions and references without measurement provenance."""
    targets = plan.get('targets', [])
    if not targets:
        raise ValueError('At least one named target region is required')
    names = set()
    for target in targets:
        name = target['name']
        if not isinstance(name, str) or not name or name in names:
            raise ValueError('Target names must be unique nonempty strings')
        names.add(name)
        roi = target['roi_xywh']
        if len(roi) != 4 or any(type(v) is not int for v in roi):
            raise ValueError('ROI must contain four integers: x, y, width, height')
        x, y, width, height = roi
        if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 384 or y + height > 288:
            raise ValueError('ROI must lie wholly in the 384 by 288 image')
        reference = target.get('reference')
        if reference is not None:
            for key in ('surface_temperature_c', 'uncertainty_c'):
                if not isinstance(reference.get(key), (int, float)) or not math.isfinite(reference[key]):
                    raise ValueError('Reference temperature and uncertainty must be finite numbers')
            if reference['uncertainty_c'] < 0:
                raise ValueError('Reference uncertainty cannot be negative')
            for key in ('instrument', 'measured_at_utc', 'method'):
                if not isinstance(reference.get(key), str) or not reference[key].strip():
                    raise ValueError(f'Reference requires {key}')
    return targets


def spatial_agreement(metrics):
    """Compare trailer fields with the actual image; center meaning stays open."""
    return {
        'high_index_equals_image_max': metrics['trailer_high_index'] == metrics['image_word_max'],
        'low_index_equals_image_min': metrics['trailer_low_index'] == metrics['image_word_min'],
        'high_coordinate_matches_index': metrics['word_at_trailer_high_xy'] == metrics['trailer_high_index'],
        'low_coordinate_matches_index': metrics['word_at_trailer_low_xy'] == metrics['trailer_low_index'],
        'center_equals_pixel_192_144': metrics['trailer_center_index'] == metrics['image_center_word_at_192_144'],
        'center_minus_pixel_192_144': metrics['trailer_center_index'] - metrics['image_center_word_at_192_144'],
    }


def analyze_frame(raw, targets):
    """Calculate a lookup once, then compare preselected region statistics."""
    summary = lookup_frame(raw)  # Validates full uint16 pixels and all native summary indices.
    table, _ = build_lookup(raw)
    words = np.frombuffer(raw, dtype='<u2', count=384 * 288).reshape(288, 384)
    matrix = temperature_matrix(raw, table)
    metrics = frame_metrics(raw)
    regions = {}
    for target in targets:
        x, y, width, height = target['roi_xywh']
        indices = words[y:y + height, x:x + width]
        values = matrix[y:y + height, x:x + width]
        region = {'raw_min_max_mean': [int(indices.min()), int(indices.max()), float(indices.mean())],
                  'lookup_min_max_mean_c': [float(values.min()), float(values.max()), float(values.mean(dtype=np.float64))],
                  'lookup_stddev_c': float(values.std(dtype=np.float64))}
        reference = target.get('reference')
        if reference is not None:
            region['mean_minus_reference_c'] = region['lookup_min_max_mean_c'][2] - reference['surface_temperature_c']
        regions[target['name']] = region
    return {'sha256': hashlib.sha256(raw).hexdigest(),
            'image_sha256': hashlib.sha256(raw[:221184]).hexdigest(), 'metrics': metrics,
            'spatial_agreement': spatial_agreement(metrics), 'lookup': summary,
            'regions': regions}


def analyze_frames(paths, plan):
    """Keep per-frame evidence and report observed drift without a calibration claim."""
    targets = validate_plan(plan)
    frames = [{'file': path.name, **analyze_frame(path.read_bytes(), targets)} for path in paths]
    if not frames:
        raise ValueError('At least one radiometric frame is required')
    image_hashes = [frame['image_sha256'] for frame in frames]
    longest = streak = 1
    for before, after in zip(image_hashes, image_hashes[1:]):
        streak = streak + 1 if before == after else 1
        longest = max(longest, streak)
    stability = {}
    for target in targets:
        values = np.array([f['regions'][target['name']]['lookup_min_max_mean_c'][2] for f in frames])
        stability[target['name']] = {'frame_count': len(frames), 'mean_c': float(values.mean()),
                                    'temporal_stddev_c': float(values.std()),
                                    'peak_to_peak_c': float(np.ptp(values)),
                                    'last_minus_first_c': float(values[-1] - values[0])}
    return {'status': 'Experimental discrimination/reference comparison; no calibration claim',
            'plan': plan, 'frames': frames, 'region_stability': stability,
            'unique_image_count': len(set(image_hashes)),
            'longest_identical_image_run': longest,
            'repeat_note': 'Identical images can indicate a held frame; low drift alone does not prove live stability.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('frames', type=Path, nargs='+')
    args = parser.parse_args()
    report = analyze_frames(args.frames, json.loads(args.plan.read_text()))
    with args.report.open('x') as file:
        json.dump(report, file, indent=2, allow_nan=False)
        file.write('\n')
    print(json.dumps(report['region_stability'], indent=2))


if __name__ == '__main__':
    main()
