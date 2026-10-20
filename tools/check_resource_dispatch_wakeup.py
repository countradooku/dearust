#!/usr/bin/env python3
"""Selected Server eventfd mask with real native epoll/eventfd interleavings.

The outbox empty observation and producer timing are modeled; readiness,
counter aggregation, ONESHOT and rearm behavior use Linux kernel APIs.
No RPC, Call construction, continuation, or complete transport claims.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from patch_resource_dispatch_wakeup import patch_dispatch_wakeup


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def mask(source):
    start=source.index('// reset the eventfd by reading from it')
    end=source.index('epoll_ctl(_epollFD, EPOLL_CTL_MOD, _wakeupFD',start)
    expressions=re.findall(r'settings\.events\s*=\s*([^;]+);',source[start:end])
    if len(expressions)!=1:raise ValueError('eventfd mask not unique')
    return 'settings.events = '+expressions[0]+';'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True,help='Owned composed server.cpp')
    parser.add_argument('--baseline',type=Path,required=True,help='Pinned original dependency repository')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    tools=Path(__file__).resolve().parent
    fixture=tools/'fixtures/resource-dispatch-wakeup-native.cpp.in'
    inputs=[args.source.resolve(),fixture,Path(__file__).resolve(),tools/'patch_resource_dispatch_wakeup.py']
    before={str(path):digest(path) for path in inputs}
    selected=args.source.read_text();fixed=patch_dispatch_wakeup(selected)
    pin=subprocess.check_output(['git','-C',str(args.baseline),'rev-parse','HEAD'],text=True).strip()
    original=subprocess.check_output(['git','-C',str(args.baseline),'show',pin+':src/server.cpp'],text=True)
    sources={'selected':selected,'original':original,'fixed':fixed}
    records=[];results={}
    for variant,flags in [('native',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all'])]:
        for label,source in sources.items():
            path=output/(variant+'-'+label+'.cpp');path.write_text(fixture.read_text().replace('@EVENTS@',mask(source)))
            binary=path.with_suffix('')
            command=['clang++','-std=c++17','-pthread','-Wall','-Wextra','-Werror',*flags]
            if label!='fixed':command.append('-DOLD_DISPATCH_MASK')
            command += [str(path),'-o',str(binary)]
            for invocation in [command,[str(binary)]]:
                result=subprocess.run(invocation,capture_output=True,text=True,timeout=60,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=1'})
                records.append(dict(command=invocation,exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr))
                (output/'commands.json').write_text(json.dumps(records,indent=2)+'\n');result.check_returncode()
            results[variant+'-'+label]=json.loads(result.stdout)
    if any(digest(Path(path))!=value for path,value in before.items()):raise ValueError('dispatch input drift')
    report=dict(passed=True,scope=__doc__,inputs_unchanged=True,input_sha256=before,
        original=dict(repository=str(args.baseline.resolve()),commit=pin),
        expressions={key:mask(source) for key,source in sources.items()},results=results,commands=records)
    (output/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(passed=True,results=results)))


if __name__=='__main__':main()
