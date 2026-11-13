#!/usr/bin/env python3
"""Run the same eleven offline UI cases in source or a native frozen executable. No hardware/CI."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def campaign(fixtures, legacy, output, binary=None):
    """Create unique evidence; preserve every failed case and never retry a configuration automatically."""
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    cases=[('lmtr-masked',fixtures/'lmtr/files/illustration-01-stored.lmtr',[]),
      ('lmtr-fullsize',fixtures/'lmtr/files/illustration-02-stored.lmtr',[]),
      ('lmtr-recover',fixtures/'lmtr/files/missing-end.lmtr',['--recover-lmtr']),
      ('lmtr-reject',fixtures/'lmtr/files/unknown-required-feature.lmtr',['--expect-window-error']),
      ('lmtx',fixtures/'lmtx/temperature-only.lmtx',[]),
      ('lmtx-preview',fixtures/'lmtx/preview-only-160x120.lmtx',[]),
      ('lmtx-mask',fixtures/'lmtx/validity-mask.lmtx',[]),
      ('lmtx-geometry',fixtures/'lmtx/alternate-7x19.lmtx',[]),
      ('lmtx-reject',fixtures/'lmtx/corrupt-sha.lmtx',['--expect-window-error']),
      ('legacy-still',legacy/'legacy palm.json',[]),
      ('legacy-recording',legacy/'legacy sequence.lmthermal',['--legacy-recording'])]
    command=[str(binary)] if binary else [sys.executable,str(ROOT/'lmthermal_desktop.py')]
    results=[]
    for name,path,flags in cases:
        folder=output/name
        with (output/(name+'.log')).open('x') as log:
            run=subprocess.run(command+['--open',str(path),*flags,'--window-check-output',str(folder)],
                               stdout=log,stderr=log,timeout=60)
        report=json.loads((folder/'window-check.json').read_text()) if (folder/'window-check.json').exists() else {}
        results.append(dict(case=name,exit=run.returncode,status=report.get('status'),error=report.get('error'),
            platform=report.get('platform'),window_exposed=report.get('window_exposed'),closed=report.get('closed_normally')))
        print(results[-1],flush=True)
    (output/'campaign.json').write_text(json.dumps(results,indent=2)+'\n')
    return all(row['status']=='passed' and row['exit']==0 for row in results)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures',type=Path,required=True);parser.add_argument('--legacy',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--binary',type=Path)
    args=parser.parse_args()
    sys.exit(0 if campaign(args.fixtures.absolute(),args.legacy.absolute(),args.output.absolute(),args.binary) else 1)
