#!/usr/bin/env python3
"""Exact-source private qualification; Windows must run natively, never inferred from Linux/WSL.

No package installation, credentials, camera, signing, release or system-policy changes.
Outputs are new, outside Git. Failed stages are retained; no automatic retry.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def preflight(expected, output, windows=False):
    """Check before any output/build mutation; a dirty/wrong checkout is not an accepted binary source."""
    if windows and sys.platform!='win32':raise ValueError('Native Windows is required; Linux/WSL does not qualify')
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if sha!=expected:raise ValueError('Wrong source commit')
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip():raise ValueError('Dirty source')
    if output.resolve().is_relative_to(ROOT.resolve()):raise ValueError('Output must be outside checkout')
    if output.exists():raise ValueError('Output must be new')
    return sha


def qualify(expected, output, *, windows=False, build=False):
    """Separate offscreen tests, frozen integrity, exposed-window events and later manual acceptance."""
    sha=preflight(expected,output,windows);output.mkdir(parents=True,exist_ok=False)
    report=dict(sha=sha,os=platform.platform(),python=sys.version,stages=[],operator_acceptance=False,camera_opened=False)
    def run(name, command, *, offscreen=False):
        """Retain each raw result including a failure/timeout before propagating non-success."""
        env=dict(os.environ)
        if offscreen:env['QT_QPA_PLATFORM']='offscreen'
        else:env.pop('QT_QPA_PLATFORM',None)
        stage=dict(name=name,command=command);report['stages'].append(stage)
        try:
            with (output/(name+'.log')).open('x',encoding='utf-8') as log:
                completed=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=900)
            stage['exit']=completed.returncode
        finally:
            (output/'qualification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        if stage['exit']:raise RuntimeError('Failed stage: '+name)
    python=sys.executable
    run('tests',[python,'-m','unittest','discover','-s','tests','-v'],offscreen=True)
    run('diff',['git','diff','--check'])
    run('inputs',[python,str(ROOT/'tools/prepare_offline_qualification.py'),'--output',str(output/'inputs é')])
    if build:
        run('build',[python,str(ROOT/'tools/build_desktop_bundle.py'),'--output',str(output/'bundle')],offscreen=True)
        binary=output/'bundle/dist/LMThermal-Desktop'/('LMThermal-Desktop.exe' if sys.platform=='win32' else 'LMThermal-Desktop')
        run('bundle-hashes',[python,str(ROOT/'tools/audit_desktop_bundle.py'),str(binary.parent),
            str(output/'bundle/bundle-sha256.txt'),'--report',str(output/'bundle-audit.json')])
        run('canonical-parity',[python,str(ROOT/'tools/check_frozen_bundle.py'),str(binary),
            '--report',str(output/'canonical-parity.json')],offscreen=True)
        run('exposed-windows',[python,str(ROOT/'tools/run_window_campaign.py'),'--fixtures',str(ROOT/'tests/fixtures'),
            '--legacy',str(output/'inputs é'),'--output',str(output/'exposed-windows'),'--binary',str(binary)])
    report['status']='passed';report['manual_window_and_installer_acceptance']='NOT RUN; use the documented operator procedure'
    (output/'qualification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-commit',required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--native-windows',action='store_true');parser.add_argument('--build',action='store_true')
    args=parser.parse_args()
    print(json.dumps(qualify(args.expected_commit,args.output.resolve(),windows=args.native_windows,build=args.build),indent=2))
