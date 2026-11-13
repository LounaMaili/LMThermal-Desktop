#!/usr/bin/env python3
"""Verify private bundle bytes/contained links and inspect Linux ABI requirements without loading libraries."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def audit(folder, manifest):
    """No install, code execution, network or source mutation; a notice list is not a legal certification."""
    folder = folder.resolve()
    checked = []
    for line in manifest.read_text(encoding='utf-8').splitlines():
        digest, name = line.split('  ', 1)
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Manifest path escapes the bundle')
        file = folder / relative
        if not file.resolve().is_relative_to(folder):
            raise ValueError('Bundle link escapes the bundle')
        if hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            raise ValueError('Bundle SHA-256 mismatch: '+name)
        checked.append(name)
    if not checked:
        raise ValueError('Empty bundle inventory')
    elf = []; seen = set(); tool = shutil.which('readelf')
    for name in checked:
        file = (folder/name).resolve()
        if file in seen:continue
        seen.add(file)
        with file.open('rb') as stream: magic = stream.read(4)
        if magic != b'\x7fELF' or not tool:continue
        versions = subprocess.check_output([tool,'--version-info',str(file)],text=True,stderr=subprocess.DEVNULL)
        required = sorted(set(re.findall(r'\bGLIBC_(\d+\.\d+)\b',versions)),key=lambda x:tuple(map(int,x.split('.'))))
        elf.append(dict(member=str(file.relative_to(folder)),glibc_versions=required))
    versions = [v for item in elf for v in item['glibc_versions']]
    highest = max(versions,key=lambda x:tuple(map(int,x.split('.')))) if versions else None
    return dict(status='passed',verified_files=len(checked),contained_links=True,
                highest_observed_glibc_requirement=highest,elf=elf,
                abi_scope='ELF symbol requirement inspection only; not clean-distro/plugin portability qualification',
                notice_files=[n for n in checked if n.startswith('THIRD_PARTY/')],
                legal_scope='Notice inventory only; actual redistributed components and obligations need review')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder',type=Path);parser.add_argument('manifest',type=Path)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    with args.report.open('x',encoding='utf-8') as stream:
        json.dump(audit(args.folder,args.manifest),stream,indent=2);stream.write('\n')
