#!/usr/bin/env python3
"""Exact C unlink against deterministic active-callback future-wait model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from patch_prepared_resource_mach_waiter import patch_mach_waiter


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    source=a.source.resolve(strict=True);output=a.output.resolve();output.mkdir(parents=True,exist_ok=False);root=Path(__file__).resolve().parent
    fixture=root/'fixtures/prepared-resource-mach-waiter-native.c.in';paths=[source,fixture,root/'patch_prepared_resource_mach_waiter.py',Path(__file__).resolve()]
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();initial={str(p):digest(p) for p in paths}
    c=patch_mach_waiter(source.read_text());start=c.index('int knote_unlink_waitq(');end=c.index('void knote_link_waitqset_lazy_alloc(',start)
    selected=output/'prepared-resource-mach-waiter-native.c';selected.write_text(fixture.read_text().replace('// UNLINK',c[start:end]));records=[]
    for variant,flags in [('native',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all'])]:
        binary=output/variant
        for cmd in [['clang','-std=gnu11','-pthread','-Wall','-Wextra','-Werror',*flags,str(selected),'-o',str(binary)],[str(binary)]]:
            env=dict(os.environ);env['ASAN_OPTIONS']='detect_leaks=1';r=subprocess.run(cmd,capture_output=True,text=True,env=env,timeout=60)
            records.append(dict(command=cmd,variant=variant,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr))
            if r.returncode:break
        if records[-1]['exit_code']:break
    report=dict(passed=all(r['exit_code']==0 for r in records) and all(digest(Path(p))==h for p,h in initial.items()),scope=__doc__+' Actual XNU callback/waitq runtime proof is separate.',input_sha256=initial,commands=records)
    (output/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
