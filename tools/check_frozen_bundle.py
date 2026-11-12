#!/usr/bin/env python3
"""Compare the native frozen CLI to source on all unchanged canonical files; no hardware."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def outcome(command):
    """Keep structured success or the terminal validation exception, not private host traceback paths."""
    result = subprocess.run(command, text=True, capture_output=True, timeout=60)
    if result.returncode == 0:
        return {'exit': 0, 'result': json.loads(result.stdout)}
    # PyInstaller appends its own execution footer; it is not a validator result.
    lines = [line for line in result.stderr.splitlines() if line.strip() and not line.startswith('[PYI-')]
    return {'exit': result.returncode, 'error': lines[-1] if lines else 'no diagnostic'}


def check(binary):
    """Verify source bytes before/after, with strict and explicit recovery paths kept separate."""
    fixture_root = ROOT / 'tests/fixtures/lmtr'
    manifest = json.loads((fixture_root / 'manifest.json').read_text())
    results = []
    for case in manifest['cases']:
        path = fixture_root / case['file']
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        assert before == case['sha256'], case['id']
        for recover in [False, True]:
            arguments = ['--verify-lmtr', str(path)] + (['--recover-lmtr'] if recover else [])
            source = outcome([sys.executable, str(ROOT / 'lmthermal_desktop.py')] + arguments)
            frozen = outcome([str(binary)] + arguments)
            assert source == frozen, (case['id'], recover, source, frozen)
            results.append(dict(case=case['id'], recovery=recover, outcome=frozen))
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    return dict(status='passed', canonical_files=len(manifest['cases']), comparisons=len(results),
                source_hashes_unchanged=True, results=results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    with args.report.open('x', encoding='utf-8') as report:
        report.write(json.dumps(check(args.binary.absolute()), indent=2)+'\n')
