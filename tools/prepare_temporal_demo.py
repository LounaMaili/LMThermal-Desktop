#!/usr/bin/env python3
"""Create a NEW explicitly synthetic comparison demo, never edit an existing LMTR.

Developer-only use of the existing fixture writer; this is not a product recorder.
The frozen canonical fixtures and their receipts remain unchanged.
"""
import argparse
from copy import deepcopy
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from lmtr_test_support import grid_plan, gap_plan
from lmtr_reference.container import write_plan


def make_plan():
    """A moving synthetic warm region and four explicit missing observations at 5 Hz."""
    plan = grid_plan(64, 48)
    plan['header']['producer']['application_id'] = 'org.example.synthetic.comparison-demo'
    template = plan['chunks'][0]
    start, observation, end = deepcopy(template['metadata']['entries'])
    gap = gap_plan()['chunks'][0]['metadata']['entries'][2]
    gap['segment_id'] = observation['segment_id']
    plan['chunks'] = []
    y, x = np.mgrid[:48, :64]
    for sample in range(60):
        chunk = deepcopy(template)
        index, stamp = sample + 1, (sample + 1) * 200_000_000
        if sample in (12, 13, 34, 48):
            entry = deepcopy(gap)
            entry.update(observation_first=str(sample), observation_last=str(sample))
            chunk['metadata']['blocks'] = []
            chunk['metadata']['contexts'] = []
            chunk['payload_recipes'] = {}
        else:
            entry = deepcopy(observation)
            entry['observation_sequence'] = str(sample)
            entry['receipt']['stamp']['ticks'] = str(stamp)
            values = (20 + 4 * x / 63 + 2 * np.sin(sample / 7) +
                      (10 + 5 * np.cos(sample / 6)) * np.exp(-((x - 22 - sample / 4)**2 + (y - 24)**2) / 180))
            chunk['payload_recipes'] = {f'{index}:temperature': {'kind': 'hex', 'hex': values.astype('<f4').tobytes().hex()}}
        entry.update(entry_id=str(index), timeline_ns=str(stamp))
        chunk['metadata']['entries'] = ([deepcopy(start)] if sample == 0 else []) + [entry]
        if sample == 59:
            end.update(entry_id='61', timeline_ns='12200000000')
            chunk['metadata']['entries'].append(end)
        plan['chunks'].append(chunk)
    return plan


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new file; existing recordings are never overwritten')
    write_plan(make_plan(), args.output)
