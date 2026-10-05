"""Validate an entire LMTX still before returning an immutable offline source.

No camera imports, thermometry recomputation, path extraction or dynamic plugins.
All optional inventory bytes are owned and hash-checked too. Explicit local
128 MiB retained-data and 8M decoded-image limits bound interactive allocations;
these resource refusals are not claims that an otherwise legal file is corrupt.
"""

import hashlib
import os
from pathlib import Path
from dataclasses import replace
import time
import numpy as np

from celsius_palette import CelsiusRange
from lmtx_container import Container, LmtxError, require
from lmtx_json import decode
from lmtx_schema import Schema, DTYPES, integer, number, obj, array, demand
from lmtx_images import decode_image
from lmtx_ht301 import validate_native, validate_evidence
from offline_measurement import (OfflineMeasurement, NativePlane, PALETTE_IDS,
                                 Transform, freeze, statistics)

LOCAL_OWNED_BYTES = 134_217_728
LOCAL_IMAGE_PIXELS = 8_388_608
LOCAL_GRID_PIXELS = 8_388_608


def _near(saved, calculated):
    require(abs(number(saved)-calculated) <= max(1e-4, 1e-6*abs(calculated)), 'Stored number disagrees with Celsius/mask', 'invalid_payload')


def _statistics(value, calculated, temperature_id):
    value = obj(value)
    demand(value.get('temperature_payload_id') == temperature_id and value.get('method') == 'finite-valid-row-major-f64-v1' and
           value.get('unit') == 'Cel', 'Invalid statistics reference/method/unit')
    require(integer(value.get('pixel_count')) == calculated.pixel_count and
            integer(value.get('valid_pixel_count')) == calculated.valid_pixel_count, 'Stored statistics counts disagree', 'invalid_payload')
    fields = ('min', 'max', 'mean', 'min_xy', 'max_xy')
    if not calculated.valid_pixel_count:
        require(not any(k in value for k in fields), 'Zero-valid statistics contain numbers/locations', 'invalid_payload')
    else:
        for key, actual in zip(fields[:3], (calculated.min_c, calculated.max_c, calculated.mean_c)): _near(value.get(key), actual)
        for key, actual in zip(fields[3:], (calculated.min_xy, calculated.max_xy)):
            coordinate = tuple(integer(v) for v in array(value.get(key)))
            require(coordinate == actual, 'Stored extrema coordinates disagree (first row-major ties)', 'invalid_payload')


def load_lmtx(path, *, cancelled=lambda: False):
    """Return data only after every ZIP, schema, integrity and binary check passes."""
    started = time.perf_counter()
    path = Path(path).absolute()
    try:
        with path.open('rb') as stream:
            before = os.fstat(stream.fileno())
            container = Container(stream, cancelled)
            manifest = decode(container.read('manifest.json'))
            schema = Schema(manifest)
            require(schema.geometry.pixel_count <= LOCAL_GRID_PIXELS,
                    'Local primary-grid limit is 8M pixels', 'resource_limit')
            expected = {p['member'] for p in schema.payloads.values()} | {'manifest.json'}
            require(set(container.records) == expected, 'Missing or undeclared payload inventory', 'missing_payload')
            total = sum(p['byte_length'] for p in schema.payloads.values())
            require(total <= LOCAL_OWNED_BYTES, 'Local retained-payload limit is 128 MiB', 'resource_limit')
            for p in schema.payloads.values():
                require(container.records[p['member']].size == p['byte_length'], 'Descriptor/ZIP byte length mismatch', 'integrity_mismatch')
            metadata_elapsed = time.perf_counter()-started
            payloads = {}
            for pid, descriptor in schema.payloads.items():
                payloads[pid] = container.read(descriptor['member'], descriptor['sha256'])
            stream.seek(0)
            digest = hashlib.sha256()
            remaining = container.size
            while remaining:
                if cancelled(): raise InterruptedError('Load cancelled')
                chunk = stream.read(min(65536, remaining))
                require(bool(chunk), 'Archive changed during read', 'integrity_mismatch')
                digest.update(chunk)
                remaining -= len(chunk)
            require(not stream.read(1), 'Archive grew during read', 'integrity_mismatch')
            after = os.fstat(stream.fileno())
            require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                    (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'Archive changed during validation', 'integrity_mismatch')
            archive_hash = digest.hexdigest()
        validated_elapsed = time.perf_counter()-started
        evidence = {}
        for extension in schema.extensions:
            if 'metadata_payload_id' in extension:
                evidence[extension['id']] = decode(payloads[extension['metadata_payload_id']])
        preview = None
        decoded_image_bytes = 0
        for descriptor in schema.payloads.values():
            if descriptor['role'] in ('preview', 'visible_image'):
                image = descriptor['image']
                require(image['width_px']*image['height_px'] <= LOCAL_IMAGE_PIXELS,
                        'Local image decoder limit is 8M pixels', 'resource_limit')
                pixels = decode_image(payloads[descriptor['id']], descriptor)
                decoded_image_bytes += len(pixels)
                require(total+decoded_image_bytes <= LOCAL_OWNED_BYTES,
                        'Local retained payload/image budget is 128 MiB', 'resource_limit')
                if descriptor['role'] == 'preview' and preview is None: preview = pixels
        temperature = payloads[schema.temperature['id']] if schema.temperature else None
        mask_bytes = payloads[schema.mask['id']] if schema.mask else None
        mask = None if mask_bytes is None else np.frombuffer(mask_bytes, dtype='u1').reshape(schema.geometry.shape)
        full = None
        if temperature is not None:
            matrix = np.frombuffer(temperature, dtype='<f4').reshape(schema.geometry.shape)
            # Inspect bounded blocks rather than allocating full-grid boolean copies.
            for start in range(0, schema.geometry.pixel_count, 8192):
                if cancelled():
                    raise InterruptedError('Load cancelled')
                values = matrix.flat[start:start+8192]
                if mask is None:
                    require(np.isfinite(values).all(), 'Nonfinite valid temperature', 'invalid_payload')
                else:
                    mask_values = mask.flat[start:start+8192]
                    valid = mask_values == 1
                    require(((mask_values == 0) | valid).all(), 'Mask bytes must be 0 or 1', 'invalid_payload')
                    require(np.isfinite(values[valid]).all() and (values.view('<u4')[~valid] == 0).all(),
                            'Nonfinite reading or noncanonical invalid +0 filler', 'invalid_payload')
            full = statistics(matrix, mask, cancelled=cancelled)
            validity = 'no_valid_pixels' if not full.valid_pixel_count else 'all_valid' if full.valid_pixel_count == full.pixel_count else 'partially_valid'
            require(schema.measurement['validity'] == validity, 'Validity class contradicts mask', 'invalid_payload')
            if 'extrema' in schema.measurement: _statistics(schema.measurement['extrema'], full, schema.temperature['id'])
            work = full.pixel_count
            for item, roi in schema.rectangles:
                if cancelled(): raise InterruptedError('Load cancelled')
                work += roi.pixel_count if 'statistics' in item else 0
                require(work <= 67_108_864, 'Local stored-statistics verification budget is 64M pixel visits', 'resource_limit')
                if 'statistics' in item: _statistics(item['statistics'], statistics(matrix, mask, roi, cancelled=cancelled), schema.temperature['id'])
            for point in manifest.get('analysis', {}).get('points', []):
                if 'temperature_c' in point:
                    x, y = point['x_px'], point['y_px']
                    require(mask is None or mask[y, x] == 1, 'Stored point reading on invalid pixel', 'invalid_payload')
                    _near(point['temperature_c'], float(matrix[y, x]))
        planes = []
        for p in schema.role('native_samples'):
            content = payloads[p['id']]
            validate_native(p, content)
            planes.append(NativePlane(p['id'], p['encoding'], DTYPES[p['dtype']][0], tuple(p['shape']), content))
        validate_evidence(schema, evidence, payloads)
        presentation = manifest.get('presentation', {})
        palette_id = presentation.get('palette_id', 'white_hot')
        bounds = presentation.get('effective_bounds')
        bounds = None if bounds is None else CelsiusRange(number(bounds['min']), number(bounds['max']))
        t = presentation.get('transform', {})
        result = OfflineMeasurement(path, 'lmthermal-exchange', schema.geometry, temperature, mask_bytes,
                                    tuple(planes), freeze(manifest), freeze(evidence), freeze(payloads), preview,
                                    archive_hash, full, schema.rectangles[0][1] if schema.rectangles else None,
                                    PALETTE_IDS.get(palette_id, 'White hot'), palette_id, palette_id not in PALETTE_IDS,
                                    bounds, presentation.get('range_mode', 'auto') == 'auto',
                                    Transform(t.get('rotation_degrees', 0), t.get('mirror_x', False), t.get('mirror_y', False)),
                                    (path,))
        ht = next((e for e in schema.extensions if e['id'] == 'org.lmthermal.camera.ht301' and e['schema_version']['major'] == 1), None)
        if ht is not None:
            observed = evidence.get(ht['id'], {}).get('observations', {}).get('trailer_center')
            if observed is not None:
                result = replace(result, reported_center=(observed['raw_index'], number(observed['native_equivalent_c'])))
        timings = {'metadata_ms': metadata_elapsed*1000, 'integrity_ms': (validated_elapsed-metadata_elapsed)*1000,
                   'binary_model_ms': (time.perf_counter()-started-validated_elapsed)*1000,
                   'total_ms': (time.perf_counter()-started)*1000,
                   'retained_payload_bytes': total, 'decoded_primary_rgb_bytes': len(preview) if preview else 0}
        return result, timings
    except LmtxError: raise
    except MemoryError as exc: raise LmtxError('resource_limit', 'Local allocation failed') from exc
    except (OSError, ValueError, KeyError, TypeError, OverflowError, IndexError) as exc:
        raise LmtxError('invalid_manifest', 'Malformed capture: '+str(exc)) from exc
