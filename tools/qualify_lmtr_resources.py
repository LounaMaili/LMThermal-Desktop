#!/usr/bin/env python3
"""On-demand noncanonical Linux R5 resource probes; never regenerates pinned corpus.

Run in the Desktop venv. Generation and fresh reader processes are separate so
writer high-water RSS cannot be presented as reader memory. RSS remains a sample,
not a portable admission proof. An optional pinned Android R3 sparse constructor
adds a valid >4GiB file without storing its zero payload blocks physically.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))


def probe(kind,path,expected=None):
    """Select exact stored evidence in a fresh Linux process, recording narrow scope."""
    from lmtr_reader import LmtrPlaybackModel
    before=path.stat();start=time.monotonic()
    model=LmtrPlaybackModel(path)
    try:
        if kind=='grid':
            frame=model.frame(1)
            assert frame.geometry.shape==(2048,2048)
            assert len(frame.temperature_bytes)==16777216
            assert frame.point(2047,2047)==frame.point(0,0)==25
            assert frame.stats.valid_pixel_count==4194304
            assert frame.roi_statistics(__import__('offline_measurement').Rectangle(0,0,2048,2048)).mean_c==25
            readings=dict(geometry=[2048,2048],float32_bytes=len(frame.temperature_bytes),constant_c=25)
        else:
            assert model.seek(int(expected['target_entry'])).kind=='unavailable'
            assert model.frame() is None  # Opaque evidence must not invent pixels.
            data=model.read_payloads(model.index)
            assert data['acquisition']==bytes(16)
            assert model.report['integrity_scope']=='opened_records'
            assert len(model.reader.validated_records)<16 and model.cache_bytes==16
            readings=dict(target_entry=model.index,target_chunk_offset=expected['target_chunk_offset'],
                          exact_zero_bytes=len(data['acquisition']))
        result=dict(status='passed',kind=kind,readings=readings,source_bytes=before.st_size,
                    allocated_bytes=before.st_blocks*512,cache_bytes=model.cache_bytes,cache_size=model.cache_size,
                    navigation_cache_bytes=model.reader.cache_bytes,validated_records=len(model.reader.validated_records),
                    file_integrity_scope=model.report['integrity_scope'],selected_chunk_integrity='fully_verified',
                    elapsed_s=time.monotonic()-start,peak_process_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    finally:model.close()
    after=path.stat()
    assert (before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns)
    result['source_stat_unchanged']=True
    result['limits']='Single synthetic sample; not a total-RSS cap or whole sparse-source hash'
    return result


def main():
    """Create private disposable sources only on explicit invocation, never import."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path);parser.add_argument('--sparse-builder',type=Path)
    parser.add_argument('--child',choices=['grid','sparse']);args=parser.parse_args()
    if args.child:
        expected=json.loads((args.directory/'sparse-expected.json').read_text()) if args.child=='sparse' else None
        print(json.dumps(probe(args.child,args.directory/(args.child+'.lmtr'),expected)));return
    os.umask(0o077);args.directory.mkdir(mode=0o700)  # Refuse existing destinations.
    from lmtr_test_support import grid_plan
    from lmtr_reference.container import write_plan
    write_plan(grid_plan(2048,2048,codec=1),args.directory/'grid.lmtr')
    kinds=['grid']
    if args.sparse_builder:
        spec=importlib.util.spec_from_file_location('sparse_constructor',args.sparse_builder)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        expected=module.construct(args.directory/'sparse.lmtr')
        (args.directory/'sparse-expected.json').write_text(json.dumps(expected,indent=2)+'\n');kinds.append('sparse')
    rows=[]
    for kind in kinds:
        process=subprocess.run([sys.executable,str(Path(__file__).resolve()),str(args.directory),'--child',kind],
                               capture_output=True,text=True,timeout=240)
        (args.directory/(kind+'-stderr.txt')).write_text(process.stderr)
        assert process.returncode==0,process.stderr
        rows.append(json.loads(process.stdout))
    (args.directory/'resource-report.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(json.dumps(rows,indent=2))


if __name__=='__main__':main()
