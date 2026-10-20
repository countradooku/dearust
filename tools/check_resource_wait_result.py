#!/usr/bin/env python3
"""Native exact-method regression for interruption versus delayed queue removal."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from patch_resource_wait_result import patch_wait_result


def function(text, signature):
    start=text.index(signature)
    body=text.index('{',start)
    end=body+1
    depth=1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}')
        end+=1
    return text[start:end]


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    paths=[a.source/'duct-tape/src/thread.c',Path('tools/fixtures/wait-result-native.cpp'),Path('tools/patch_resource_wait_result.py'),Path(__file__)]
    hashes={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    original=paths[0].read_text();fixed=patch_wait_result(original)
    sig='boolean_t thread_unblock(thread_t xthread, wait_result_t wresult)'
    funcs=[function(original,sig).replace('boolean_t thread_unblock(', 'boolean_t old_thread_unblock(',1),function(fixed,sig),function(original,'void dtape_thread_sigexc_enter('),function(original,'void dtape_thread_dying(')]
    source=a.output/'native.cpp';source.write_text(paths[1].read_text().replace('// @@EXACT_FUNCTIONS@@','\n\n'.join(funcs)))
    records=[]
    for name,flags in [('native',[]),('sanitized',['-fsanitize=address,undefined','-fno-omit-frame-pointer'])]:
        binary=a.output/name
        for cmd in [['clang++','-std=c++17','-O1','-g','-pthread',*flags,str(source),'-o',str(binary)],[str(binary.resolve())]]:
            r=subprocess.run(cmd,text=True,capture_output=True,timeout=60)
            records.append(dict(command=cmd,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr))
            (a.output/'verification.json').write_text(json.dumps(dict(passed=False,input_sha256=hashes,commands=records),indent=2)+'\n')
            if r.returncode:raise SystemExit(r.stderr+r.stdout)
    unchanged=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in hashes.items())
    (a.output/'verification.json').write_text(json.dumps(dict(passed=unchanged,inputs_unchanged=unchanged,input_sha256=hashes,commands=records,scope='Exact selected sigexc/dying/unblock methods; native locks and queue-removal retry modeled. No RPC, guest or complete scheduler proof.'),indent=2)+'\n')
    print(a.output/'verification.json')


if __name__=='__main__':main()
