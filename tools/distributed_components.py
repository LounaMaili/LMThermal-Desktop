"""Map actual collected native members to build inputs; retain matching Debian notices when available.

This is a provenance ledger, not legal certification. Unmapped/generated inputs remain explicit.
PyInstaller may rewrite RPATHs, so retain source and delivered hashes separately.
"""
import ast
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys


def collected_members(toc):
    """Read data-only PyInstaller COLLECT tuples; never execute the build's Python text."""
    data = ast.literal_eval(toc.read_text(encoding='utf-8'))
    for name, source, kind in data[0]:
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Collected member escapes bundle')
        yield name, Path(source), kind


def write_native_inventory(folder, toc, *, distributions=None, use_dpkg=True):
    """Only actual BINARY/EXTENSION entries are mapped; installed build tools are not called runtime dependencies."""
    wheels = {}
    for dist in importlib.metadata.distributions() if distributions is None else distributions:
        for file in dist.files or []:
            wheels[Path(dist.locate_file(file)).resolve()] = dict(provider='wheel',
                package=dist.metadata['Name'], version=dist.version)
    native = []; copied = {}; dpkg = shutil.which('dpkg-query') if use_dpkg else None
    notices = folder / 'THIRD_PARTY/native-debian'
    for name, source, kind in collected_members(toc):
        if kind not in ('BINARY','EXTENSION'):continue
        target = folder / '_internal' / name
        if not target.is_file():raise ValueError('Missing collected native member: '+name)
        if not target.resolve().is_relative_to(folder.resolve()):raise ValueError('Native member escapes bundle')
        item = dict(member='_internal/'+name, kind=kind,
            delivered_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            origin=wheels.get(source.resolve(),dict(provider='unmapped',source_basename=source.name)))
        if item['origin']['provider']=='unmapped' and dpkg:
            found = subprocess.run([dpkg,'-S',str(source)],text=True,capture_output=True)
            if found.returncode == 0:
                package = found.stdout.splitlines()[0].rsplit(': ',1)[0]
                version = subprocess.check_output([dpkg,'-W','-f=${Version}',package],text=True)
                item['origin'] = dict(provider='deb',package=package,version=version)
                copyright = Path('/usr/share/doc') / package.split(':')[0] / 'copyright'
                if copyright.is_file():
                    notices.mkdir(parents=True,exist_ok=True)
                    destination = notices / (package.replace(':','_')+'-copyright')
                    if package not in copied:
                        shutil.copyfile(copyright,destination);copied[package]=str(destination.relative_to(folder))
                    item['origin']['notice'] = copied[package]
        if item['origin']['provider']=='unmapped' and source.resolve().is_relative_to(Path(sys.base_prefix).resolve()):
            item['origin'] = dict(provider='CPython',version=sys.version.split()[0],notice='THIRD_PARTY/Python-LICENSE.txt')
        native.append(item)
    report = dict(native_members=len(native),unmapped=sum(x['origin']['provider']=='unmapped' for x in native),
                  members=native,scope='Actual native COLLECT provenance; not complete source/relink or legal acceptance')
    (folder/'THIRD_PARTY/native-components.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report
