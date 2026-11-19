#!/usr/bin/env python3
"""Verify private bundle bytes/contained links and inspect Linux ABI requirements without loading libraries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def elf_requirements(version_output):
    """Only undefined/needed versions constrain consumers; supplied GLIBCXX versions are not requirements."""
    needs = version_output.split('Version needs section', 1)
    values = re.findall(r'Name: ((?:GLIBC|GLIBCXX|CXXABI)_[A-Za-z0-9_.]+)', needs[1]) if len(needs) == 2 else []
    return sorted(set(values))


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
        if name in checked:
            raise ValueError('Duplicate manifest member: '+name)
        checked.append(name)
    if not checked:
        raise ValueError('Empty bundle inventory')
    members = list(folder.rglob('*'))
    for member in members:
        if member.is_symlink() and not member.resolve().is_relative_to(folder):
            raise ValueError('Bundle link escapes the bundle')
    actual = {p.relative_to(folder).as_posix() for p in members if p.is_file()}
    if actual != set(checked):
        raise ValueError('Bundle inventory omits or invents files')
    elf = []; seen = set(); tool = shutil.which('readelf')
    for name in checked:
        file = (folder/name).resolve()
        if file in seen:continue
        seen.add(file)
        with file.open('rb') as stream: magic = stream.read(4)
        if magic != b'\x7fELF':continue
        if not tool:raise RuntimeError('readelf is required to audit ELF files')
        def inspect(option):
            """Pin readelf's language so localized hosts cannot silently lose version evidence."""
            return subprocess.check_output([tool,option,str(file)],text=True,stderr=subprocess.DEVNULL,
                                           env={**os.environ,'LC_ALL':'C'})
        versions = inspect('--version-info')
        symbols = elf_requirements(versions)
        required = sorted(set(s.removeprefix('GLIBC_') for s in symbols if re.fullmatch(r'GLIBC_\d+(?:\.\d+)+',s)),
                          key=lambda x:tuple(map(int,x.split('.'))))
        dynamic = inspect('--dynamic')
        header = inspect('--file-header')
        elf.append(dict(member=str(file.relative_to(folder)),glibc_versions=required,
                        required_symbol_versions=symbols,
                        needed=re.findall(r'\(NEEDED\).*?\[(.*?)\]',dynamic),
                        search_paths=re.findall(r'\((?:RPATH|RUNPATH)\).*?\[(.*?)\]',dynamic),
                        machine=re.search(r'Machine:\s*(.*)',header).group(1).strip()))
    versions = [v for item in elf for v in item['glibc_versions']]
    highest = max(versions,key=lambda x:tuple(map(int,x.split('.')))) if versions else None
    return dict(status='passed',verified_files=len(checked),contained_links=True,
                highest_observed_glibc_requirement=highest,elf=elf,
                abi_scope='Every inventoried ELF: needed GLIBC/GLIBCXX/CXXABI versions and dynamic dependencies; not runtime qualification',
                notice_files=[n for n in checked if n.startswith('THIRD_PARTY/')],
                legal_scope='Notice inventory only; actual redistributed components and obligations need review')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder',type=Path);parser.add_argument('manifest',type=Path)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    with args.report.open('x',encoding='utf-8') as stream:
        json.dump(audit(args.folder,args.manifest),stream,indent=2);stream.write('\n')
