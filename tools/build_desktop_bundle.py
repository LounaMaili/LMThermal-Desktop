#!/usr/bin/env python3
"""Build a private native-OS prototype into a NEW directory; never publish or modify sources."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def write_inventory(folder):
    """Retain installed distribution metadata/license texts; legal completeness needs human review."""
    notices = folder / 'THIRD_PARTY'
    notices.mkdir()
    inventory = []
    for dist in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = dist.metadata['Name']
        retained = []
        for member in dist.files or []:
            lower = str(member).lower()
            if '.dist-info/' in lower and any(word in Path(lower).name for word in ('license', 'copying', 'notice')):
                source = Path(dist.locate_file(member))
                if source.is_file():
                    # Keep original relative dist-info structure so duplicate basenames cannot overwrite.
                    if Path(member).is_absolute() or '..' in Path(member).parts:
                        raise ValueError('Unsafe distribution notice path')
                    target = notices / str(member)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    retained.append(str(member))
        inventory.append(dict(name=name, version=dist.version, license=dist.metadata.get('License-Expression')
            or dist.metadata.get('License'), notices=retained))
    (notices / 'inventory.json').write_text(json.dumps(inventory, indent=2)+'\n', encoding='utf-8')


def build(output):
    """Run only native host build/self-check, record reproducibility inputs, and create no installer side effects."""
    output = output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
        '--distpath', str(output / 'dist'), '--workpath', str(output / 'work'),
        str(ROOT / 'packaging/desktop.spec')], cwd=ROOT, check=True)
    folder = output / 'dist/LMThermal-Desktop'
    write_inventory(folder)
    for name in ['PACKAGING.md', 'WINDOWS_DEVELOPMENT.md', 'PRODUCT_WORKSPACE.md']:
        shutil.copyfile(ROOT / 'docs' / name, folder / name)
    shutil.copyfile(ROOT / 'packaging/README-RUNTIME.txt', folder / 'START_HERE.txt')
    executable = folder / ('LMThermal-Desktop.exe' if sys.platform == 'win32' else 'LMThermal-Desktop')
    result = subprocess.run([str(executable), '--self-check'], text=True, capture_output=True, check=True)
    report = json.loads(result.stdout)
    assert report['status'] == 'passed' and report['frozen'] and not report['camera_opened']
    (output / 'self-check.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)
    inputs = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).split(b'\0')
    source_lines = []
    for name in sorted(set(x.decode('utf-8') for x in inputs if x)):
        source = ROOT / name
        if source.is_file():
            source_lines.append(hashlib.sha256(source.read_bytes()).hexdigest()+'  '+name)
    (output / 'source-files-sha256.txt').write_text('\n'.join(source_lines)+'\n', encoding='utf-8')
    receipt = dict(source_commit=commit, uncommitted_changes=bool(dirty), platform=platform.platform(),
                   python=platform.python_version(), self_check=report,
                   qualification='Private native-host prototype; not a public release or portability certification')
    (output / 'build-receipt.json').write_text(json.dumps(receipt, indent=2)+'\n', encoding='utf-8')
    lines = []
    for file in sorted(folder.rglob('*')):
        if file.is_file():
            lines.append(hashlib.sha256(file.read_bytes()).hexdigest()+'  '+file.relative_to(folder).as_posix())
    (output / 'bundle-sha256.txt').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    if sys.platform.startswith('linux'):
        with tarfile.open(output / 'LMThermal-Desktop-linux-private.tar.gz', 'w:gz') as archive:
            archive.add(folder, arcname=folder.name)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New private output directory')
    print(json.dumps(build(parser.parse_args().output), indent=2))
