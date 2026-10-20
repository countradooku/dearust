#!/usr/bin/env python3
"""Exact transformed Open/publication ownership boundaries, adapted transport."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from patch_prepared_resource_kq_ownership import patch_kq_ownership


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',type=Path,required=True,help='new lifecycle native candidate with transformed sources')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();candidate=args.candidate.resolve(strict=True);output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parent
    inputs=[root/'patch_prepared_resource_kq_ownership.py',Path(__file__).resolve(),root/'fixtures/prepared-resource-kq-ownership-native.cpp.in',candidate/'src/call.cpp',candidate/'src/process.cpp']
    digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    initial={str(p):digest(p) for p in inputs}
    def method(text,signature):
        begin=text.index(signature);return text[begin:text.index('\n};',begin)+3]
    fixture=inputs[2].read_text()
    fixture='#include <sys/socket.h>\n'+fixture
    for anchor,filename,signature in [('// REGISTER','process.cpp','void DarlingServer::Process::registerKqchan('),('// MACH_OPEN','call.cpp','void DarlingServer::Call::KqchanMachPortOpen::processCall()'),('// PROC_OPEN','call.cpp','void DarlingServer::Call::KqchanProcOpen::processCall()')]:
        fixture=fixture.replace(anchor,method((candidate/'src'/filename).read_text(),signature))
    selected=output/'prepared-resource-kq-ownership-native.cpp';selected.write_text(fixture)
    records=[]
    for variant,flags in [('native',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all'])]:
        binary=output/variant
        for command in [['clang++','-std=c++17','-Wall','-Wextra','-Werror','-pthread',*flags,str(selected),'-o',str(binary)],[str(binary)]]:
            env=dict(os.environ);env['ASAN_OPTIONS']='detect_leaks=1:alloc_dealloc_mismatch=1'
            result=subprocess.run(command,capture_output=True,text=True,env=env,timeout=60)
            records.append(dict(command=command,variant=variant,exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr))
            if result.returncode:break
        if records[-1]['exit_code']:break
    report=dict(passed=all(r['exit_code']==0 for r in records),scope=__doc__,input_sha256=initial,commands=records)
    report['passed'] &= all(digest(Path(p))==h for p,h in initial.items())
    (output/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
