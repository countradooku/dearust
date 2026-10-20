#!/usr/bin/env python3
"""Native valid Mach kq notification, acknowledgement and seqpacket backpressure."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from patch_prepared_resource_mach_notifications import FIELDS


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();source=args.source.resolve(strict=True);output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parent
    fixture=root/'fixtures/prepared-resource-mach-notifications-native.cpp.in';core=root/'prepared-resource-mach-notifications.inc'
    packet=root/'prepared-resource-process-notifications.inc';supplement=source/'include/darlingserver/rpc-supplement.h'
    inputs=[Path(__file__).resolve(),root/'patch_prepared_resource_mach_notifications.py',fixture,core,packet,supplement]
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();initial={str(p):digest(p) for p in inputs}
    text=fixture.read_text().replace('@SUPPLEMENT@',json.dumps(str(supplement)))
    fields=FIELDS.replace(' override;',';')
    for anchor,value in [('// FIELDS',fields),('// PACKET',packet.read_text()[:packet.read_text().index('void DarlingServer::Kqchan::Process::_notifyPreparedExit')]),('// METHODS',core.read_text())]:text=text.replace(anchor,value)
    selected=output/'prepared-resource-mach-notifications-native.cpp';selected.write_text(text);records=[]
    for variant,flags in [('native',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all'])]:
        binary=output/variant
        for cmd in [['clang++','-D_GNU_SOURCE','-std=c++17','-pthread','-Wall','-Wextra','-Werror',*flags,str(selected),'-o',str(binary)],[str(binary)]]:
            env=dict(os.environ);env['ASAN_OPTIONS']='detect_leaks=1:alloc_dealloc_mismatch=1';r=subprocess.run(cmd,capture_output=True,text=True,env=env,timeout=60)
            records.append(dict(command=cmd,variant=variant,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr))
            if r.returncode:break
        if records[-1]['exit_code']:break
    report=dict(passed=all(r['exit_code']==0 for r in records) and all(digest(Path(p))==h for p,h in initial.items()),scope=__doc__+' Exact transport/read/modify/completion methods; adapted dtape event state, kernel work scheduling and monitor; actual kernel validation separate.',input_sha256=initial,commands=records)
    (output/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
