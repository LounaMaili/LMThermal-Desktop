#!/usr/bin/env python3
"""Generate PRIVATE legacy examples from existing sanitized fixtures; never creates canonical LMTR bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))


def prepare(output):
    """Refuse reused/in-checkout destinations, preserving all original fixtures and user captures."""
    output=output.resolve()
    if output.is_relative_to(ROOT):raise ValueError('Qualification inputs must be outside the checkout')
    output.mkdir(parents=True,exist_ok=False)
    from playback_support import observations, build_bundle
    from celsius_palette import CelsiusRange
    from radiometric_export import export_capture, snapshot_capture
    frames=observations()
    export_capture(snapshot_capture(frames[1],'Turbo',CelsiusRange(25,45),False),output/'legacy palm')
    build_bundle(output/'legacy sequence.lmthermal',frames)
    files=[dict(path=p.relative_to(output).as_posix(),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
           for p in sorted(output.rglob('*')) if p.is_file()]
    report=dict(provenance='Noncanonical fixture-derived examples, simulated historical gaps/drops; no new acquisition',files=files)
    (output/'qualification-inputs.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    print(json.dumps(prepare(parser.parse_args().output),indent=2))
