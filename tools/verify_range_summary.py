#!/usr/bin/env python3
"""Verify summary CSV independently via a payload-verified detailed comparison CSV.

The first pass redecodes original LMTR Float32 bytes (stdlib struct, no product
sampler). This pass aggregates only those verified detailed scalars using stdlib
arithmetic and integer predicates; it never imports the product range calculator.
Run without -O. Recovery and incompatible-pair exports need the targeted tests.
"""
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.verify_comparison_csv import verify as verify_detailed, exact


def verify_summary(source, detailed_csv, summary_csv):
    evidence = verify_detailed(source, detailed_csv)
    groups = defaultdict(list)
    with detailed_csv.open(newline='', encoding='utf-8') as data:
        for row in csv.DictReader(data):
            key = (row['row_kind'], row['selection_id']) if row['row_kind'] == 'selection' else (
                'difference', row['left_selection_id'], row['right_selection_id'])
            groups[key].append(row)
    with summary_csv.open(newline='', encoding='utf-8') as data:
        rows = list(csv.DictReader(data))
    assert 1 <= len(rows) <= 5
    bounds = None
    seen, checked = set(), []
    for row in rows:
        a, b = int(row['range_start_ns']), int(row['range_end_ns'])
        assert a <= b
        assert bounds is None or bounds == (a, b)
        bounds = a, b
        assert row['bounds_inclusive'] == 'both'
        assert row['aggregation'] == 'arithmetic_mean_of_valid_observations'
        key = (row['row_kind'], row['selection_id']) if row['row_kind'] == 'selection' else (
            'difference', row['left_selection_id'], row['right_selection_id'])
        assert key in groups and key not in seen
        seen.add(key)
        first = groups[key][0]
        assert int(first['first_timeline_ns']) <= a <= b <= max(int(r['timeline_ns']) for rs in groups.values() for r in rs)
        for column in ('recording_id', 'timeline_clock_domain_id', 'stream_id', 'selected_session_id', 'file_status', 'end_status', 'unknown_tail'):
            assert row[column] == first[column], column
        if row['row_kind'] == 'selection':
            assert row['selection_name'] == first['selection_name']
            assert row['metric'] == ('roi_mean_temperature' if first['selection'] == 'roi' else 'point_temperature')
            for column in ('x1', 'y1', 'x2_exclusive', 'y2_exclusive', 'width_px', 'height_px'):
                assert row[column] == first[column]
        else:
            assert row['metric'] == 'scalar_difference'
            right = groups['selection', row['right_selection_id']][0]
            assert row['right_stream_id'] == right['stream_id']
            assert row['right_session_id'] == right['selected_session_id']
        selected = [r for r in groups[key] if a <= int(r['timeline_ns']) <= b]
        observations = [r for r in selected if r['event'] == 'observation']
        valid = [r for r in observations if r['status'] == 'valid']
        values = [float(r['temperature_c']) for r in valid]
        gaps = [r for r in selected if r['event'] == 'gap']
        incompatible = sum(r['status'] in ('other_native_space', 'incompatible_observation') or
                           r['left_status'] == 'other_native_space' or r['right_status'] == 'other_native_space'
                           for r in observations)
        # Explicit sequential binary64 sum, same documented arithmetic contract,
        # independently implemented and fed by separately verified payload values.
        total = 0.0
        for value in values:
            total += value
        for column, expected in (('minimum_c', min(values) if values else None),
                                 ('maximum_c', max(values) if values else None),
                                 ('mean_c', total / len(values) if values else None)):
            exact(row[column], expected)
        counts = dict(valid_observations=len(valid), unavailable_observations=len(observations)-len(valid),
            incompatible_observations=incompatible, gap_events=len(gaps),
            known_missing_observations=sum(int(r['gap_count']) for r in gaps if r['gap_count'] != ''),
            unknown_gap_events=sum(r['gap_count'] == '' for r in gaps),
            control_events=len(selected)-len(observations)-len(gaps))
        for column, expected in counts.items():
            assert row[column] == str(expected), (column, row[column], expected)
        assert row['first_valid_ns'] == (valid[0]['timeline_ns'] if valid else '')
        assert row['last_valid_ns'] == (valid[-1]['timeline_ns'] if valid else '')
        assert row['status'] == ('valid' if valid else 'no_usable_data')
        assert row['compatibility_reason'] == ''
        assert row['compatibility'] == ('contains_incompatible_observations' if incompatible else
                                        'same_observation' if row['row_kind'] == 'difference' else 'selected_native_space')
        checked.append(dict(identity=key, **counts, minimum_c=row['minimum_c'], maximum_c=row['maximum_c'], mean_c=row['mean_c']))
    assert seen == set(groups), 'Summary must contain every selection and enabled compatible difference'
    return dict(status='passed', range_start_ns=str(bounds[0]), range_end_ns=str(bounds[1]),
                bounds_inclusive='both', summary_rows=len(rows), detailed_evidence=evidence, results=checked)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('detailed_csv', type=Path)
    parser.add_argument('summary_csv', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = verify_summary(args.source, args.detailed_csv, args.summary_csv)
    with args.report.open('x', encoding='utf-8') as output:
        json.dump(result, output, indent=2)
        output.write('\n')
    print(json.dumps(result))
