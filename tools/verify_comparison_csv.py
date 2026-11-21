#!/usr/bin/env python3
"""Independently check a finalized LMTR comparison CSV against original payload bytes.

Uses the pinned reference container validator plus stdlib struct/arithmetic; never
imports Desktop playback, temporal projection, statistics, NumPy or Qt. This is a
bounded diagnostic, not an exporter or a repaired recording. Recovery is out of scope.
"""
import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import struct
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lmtr_reference import Reader


def exact(actual, expected):
    """Hex comparison includes signed zero and every preserved binary64 bit."""
    if expected is None:
        assert actual == '', (actual, expected)
    else:
        assert float(actual).hex() == expected.hex(), (actual, expected)


def source_hash(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def sample(entry, blocks, row):
    """Sequential row-major valid-only arithmetic, independent of the product sampler."""
    width, height = int(row['width_px']), int(row['height_px'])
    x1, y1, x2, y2 = (int(row[k]) for k in ('x1', 'y1', 'x2_exclusive', 'y2_exclusive'))
    assert 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height
    measurement = entry['measurement']
    descriptors = {p['id']: p for p in entry['payloads']}
    plane = descriptors[measurement['temperature_payload_id']]
    assert plane['kind'] == 'materialized' and plane['dtype'] == 'f32'
    raw, offset = blocks[plane['block_id']], int(plane['offset'])
    mask = descriptors.get(measurement.get('mask_payload_id'))
    count, total, minimum, maximum, point = 0, 0.0, None, None, None
    for y in range(y1, y2):
        for x in range(x1, x2):
            index = y * width + x
            if mask and blocks[mask['block_id']][int(mask['offset']) + index] != 1:
                continue
            value = struct.unpack_from('<f', raw, offset + 4 * index)[0]
            minimum = value if minimum is None or value < minimum else minimum
            maximum = value if maximum is None or value > maximum else maximum
            total += value
            point = value
            count += 1
    if row['selection'] == 'point':
        assert (x2 - x1) * (y2 - y1) == 1
        return point, None, None, count, 1
    assert row['selection'] == 'roi'
    return total / count if count else None, minimum, maximum, count, (x2 - x1) * (y2 - y1)


def verify(source, csv_path):
    """Check each represented source event once; never retain temperature histories."""
    before = source_hash(source)
    by_event, named, seen_ids, difference_pairs = defaultdict(list), {}, defaultdict(list), set()
    with csv_path.open(newline='', encoding='utf-8') as data:
        for number, row in enumerate(csv.DictReader(data), 1):
            if number > 250_000:
                raise ValueError('Comparison CSV exceeds the diagnostic row limit')
            by_event[int(row['entry_id'])].append(row)
            if row['row_kind'] == 'selection':
                key = row['selection_id']
                assert key
                identity = tuple(row[k] for k in ('selection_name', 'stream_id', 'selected_session_id',
                                 'selection', 'x1', 'y1', 'x2_exclusive', 'y2_exclusive', 'width_px', 'height_px'))
                assert named.setdefault(key, identity) == identity
                seen_ids[key].append(int(row['entry_id']))
            elif row['row_kind'] == 'difference':
                difference_pairs.add((row['left_selection_id'], row['right_selection_id']))
            else:
                raise ValueError('Unknown comparison row kind')
    assert 1 <= len(named) <= 4 and by_event
    assert len(difference_pairs) <= 1
    assert all(ids == sorted(set(ids)) for ids in seen_ids.values())
    checked = observations = gaps = 0
    first_ns = None
    with Reader(source) as reader:
        summary = reader.check(full=True)
        cursor = 0
        while cursor < reader.size:
            record = reader.record(cursor)
            cursor += record['length']
            if record['kind'] != 2:
                continue
            meta, _ = reader.decode(record)
            prefix = 4 + struct.unpack_from('<I', record['body'])[0]
            blocks = {}
            for b in meta['blocks']:
                start = prefix + int(b['offset'])
                stored = record['body'][start:start + int(b['stored'])]
                # LMTR DEFLATE uses the RFC1950 zlib wrapper, already bounded and
                # integrity-checked above by the reference validator.
                blocks[b['id']] = stored if b['codec'] == 0 else zlib.decompress(stored)
                assert len(blocks[b['id']]) == int(b['decoded'])
            segments = {s['id']: s for s in meta['segments']}
            for entry in meta['entries']:
                first_ns = int(entry['timeline_ns']) if first_ns is None else first_ns
                observations += entry['kind'] == 'observation'
                gaps += entry['kind'] == 'gap'
                rows = by_event.pop(int(entry['entry_id']), [])
                streams = {s['stream_id'] for s in segments[entry['segment_id']]['streams']}
                expected_ids = {key for key, identity in named.items() if identity[1] in streams and
                                entry.get('stream_id', identity[1]) == identity[1]}
                assert {r['selection_id'] for r in rows if r['row_kind'] == 'selection'} == expected_ids
                expected_differences = sum(a in expected_ids and b in expected_ids for a, b in difference_pairs)
                assert len(rows) == len(expected_ids) + expected_differences
                calculated = {}
                for row in rows:
                    assert row['recording_id'] == reader.head['recording_id']
                    assert row['timeline_clock_domain_id'] == reader.head['timeline']['clock_domain_id']
                    assert row['timeline_ns'] == entry['timeline_ns']
                    assert int(row['elapsed_ns']) == int(entry['timeline_ns']) - first_ns
                    assert int(row['first_timeline_ns']) == first_ns
                    assert row['event'] == entry['kind'] and row['segment_id'] == entry['segment_id']
                    assert row['measurement_status'] == entry.get('measurement', {}).get('status', '')
                    assert row['unknown_tail'] == 'false' and row['end_status'] == summary['declared_end_status']
                    scheduled = entry.get('scheduled') or {}
                    for part in ('first_slot', 'last_slot', 'first_deadline_ns', 'last_deadline_ns'):
                        assert row['scheduled_' + part] == str(scheduled.get(part, ''))
                    receipt = entry.get('receipt') or {}
                    assert row['receipt_status'] == receipt.get('status', '')
                    stamp = receipt.get('stamp') or {}
                    assert row['receipt_ticks'] == str(stamp.get('ticks', ''))
                    assert row['receipt_clock_domain_id'] == str(stamp.get('clock_domain_id', ''))
                    if entry['kind'] == 'gap':
                        assert row['gap_category'] == entry['category'] and row['reason'] == entry['reason']
                        assert row['gap_count'] == ('' if entry['count'] is None else str(entry['count']))
                        assert row['gap_count_status'] == ('unknown' if entry['count'] is None else 'known')
                    if row['row_kind'] != 'selection':
                        continue
                    stream = next(s for s in segments[entry['segment_id']]['streams'] if s['stream_id'] == row['stream_id'])
                    assert row['session_id'] == stream['session_id']
                    period = stream['sampling'].get('period_ns')
                    assert row['period_ns'] == ('' if period is None else str(period))
                    values = (None, None, None, None, None)
                    status = entry['kind']
                    if entry['kind'] == 'observation':
                        assert entry['stream_id'] == row['stream_id']
                        grid = stream['geometry']
                        if stream['session_id'] != row['selected_session_id'] or grid is None or (
                                grid['width_px'], grid['height_px']) != (int(row['width_px']), int(row['height_px'])):
                            status = 'other_native_space'
                        elif entry['measurement']['status'] != 'available':
                            status = 'measurement_unavailable'
                        else:
                            values = sample(entry, blocks, row)
                            status = 'valid' if values[3] else 'no_valid_pixels'
                    assert row['status'] == status
                    for column, value in zip(('temperature_c', 'roi_min_c', 'roi_max_c'), values):
                        exact(row[column], value)
                    for column, value in zip(('valid_pixels', 'total_pixels'), values[3:]):
                        assert row[column] == ('' if value is None else str(value))
                    calculated[row['selection_id']] = (values[0], status, row['stream_id'], row['selected_session_id'])
                for row in rows:
                    if row['row_kind'] == 'difference':
                        a, b = calculated[row['left_selection_id']], calculated[row['right_selection_id']]
                        assert row['left_selection_id'] != row['right_selection_id'] and a[2:] == b[2:]
                        exact(row['left_temperature_c'], a[0]); exact(row['right_temperature_c'], b[0])
                        assert row['left_status'] == a[1] and row['right_status'] == b[1]
                        valid = a[1] == b[1] == 'valid'
                        exact(row['temperature_c'], a[0] - b[0] if valid else None)
                        assert row['status'] == ('valid' if valid else 'operands_unavailable')
                    else:
                        assert row['row_kind'] == 'selection'
                    checked += 1
    assert not by_event
    assert source_hash(source) == before
    return dict(status='passed', csv_rows=checked, selections=len(named), source_observations=observations,
                source_gap_events=gaps, source_sha256=before, source_unchanged=True,
                arithmetic='Independent struct Float32 decoding and sequential binary64 accumulation/subtraction',
                selection_names={key: identity[0] for key, identity in named.items()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.source, args.csv)
    with args.report.open('x', encoding='utf-8') as report:
        report.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
