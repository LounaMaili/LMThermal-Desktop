#!/usr/bin/env python3
"""Camera-free real Android source-proof parity, render and immutability check.

Private captures/proofs remain operator files. Only the curated numeric report
is suitable for repository documentation; no scene image or host path is emitted.
"""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import statistics as stat
import sys
from tempfile import TemporaryDirectory
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lmtx_reader import load_lmtx, _statistics
from lmtx_schema import Schema
from offline_measurement import save_offline_png


def check(path, proof_path, repeats=5):
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    proof = json.loads(proof_path.read_text())
    times = []
    renders = []
    tracemalloc.start()
    for _ in range(repeats):
        source, timing = load_lmtx(path)
        times.append(timing)
        assert source.manifest['capture_id'] == proof['capture_id']
        assert source.manifest['acquisition']['sequence'] == str(proof['sequence'])
        assert (source.geometry.width, source.geometry.height) == (proof['width_px'], proof['height_px'])
        assert hashlib.sha256(source.temperature_c.tobytes()).hexdigest() == proof['temperature_source_sha256']
        assert source.temperature_c.tobytes() == source.temperature_bytes
        assert source.metadata['presentation'] == proof['presentation']
        assert [source.roi.x1, source.roi.y1, source.roi.x2, source.roi.y2] == proof['roi_bounds']
        roi = source.roi_statistics(source.roi)
        _statistics(proof['roi_statistics'], roi, source.manifest['measurement']['temperature_payload_id'])
        schema = Schema(source.metadata)
        native = schema.role('native_samples')[0]
        transport = schema.role('acquisition')[0]
        assert source.raw14.tobytes() == source.payload_bytes[native['id']]
        assert source.payload_bytes[transport['id']][:len(source.raw14.tobytes())] == source.raw14.tobytes()
        assert 'org.lmthermal.camera.ht301' in source.evidence
        started = time.perf_counter()
        for palette in ('White hot','Black hot','Inferno','Iron-like','Turbo'):
            source.render(palette, source.original_bounds)
        renders.append((time.perf_counter()-started)*1000/5)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    with TemporaryDirectory() as root:
        save_offline_png(source, Path(root)/'rerender.png', 'Turbo', source.original_bounds)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    return {
        'source': 'Actual Android SAF export; independent Android source-bit proof',
        'geometry': [source.geometry.width, source.geometry.height],
        'float32_source_sha256': proof['temperature_source_sha256'],
        'exact_float32_bits_match': True,
        'raw14_min_max': [int(source.raw14.min()), int(source.raw14.max())],
        'native_bytes': source.raw14.nbytes,
        'transport_bytes': len(source.payload_bytes[transport['id']]),
        'roi': proof['roi_bounds'], 'roi_statistics': asdict(roi),
        'presentation': proof['presentation'], 'ht_metadata_retained': True,
        'archive_unchanged_after_analysis_render_png': True,
        'load_median_ms': {k: stat.median(t[k] for t in times) for k in
                           ('metadata_ms','integrity_ms','binary_model_ms','total_ms')},
        'render_per_palette_median_ms': stat.median(renders),
        'retained_payload_bytes': times[-1]['retained_payload_bytes'],
        'decoded_primary_rgb_bytes': times[-1]['decoded_primary_rgb_bytes'],
        'tracemalloc_peak_bytes': peak,
        'repetitions': repeats,
        'physical_accuracy_warning': source.accuracy_warning,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('android_source_proof', type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.capture, args.android_source_proof), indent=2))


if __name__ == '__main__': main()
