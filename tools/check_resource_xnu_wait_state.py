#!/usr/bin/env python3
"""Exact selected C dispatcher/block/unblock methods, modeled waitq and native gate.

No RPC, closure lifetime, Checkin faults, wait-tag redesign, or whole-scheduler
safety claim. The original loss is reproduced before applying the transform.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from patch_resource_xnu_wait_state import patch_xnu_wait_state


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def method(source,start,end):
    begin=source.index(start);return source[begin:source.index(end,begin)].strip()
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--libsimple-source',type=Path,default=Path('/home/radud/darling-lol/build/darling-src/src/libsimple'))
    p.add_argument('--baseline',type=Path,default=Path('/home/radud/Documents/Codex/2026-10-18/re/work/full-baseline/src/external/darlingserver'))
    args=p.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    tools=Path(__file__).resolve().parent
    source=args.source.resolve()/'duct-tape/src/thread.c'
    fixture=tools/'fixtures/resource-xnu-wait-state-native.c.in';patch=tools/'patch_resource_xnu_wait_state.py'
    simple=args.libsimple_source.resolve();lock=simple/'src/lock.c'
    hashes={str(path):digest(path) for path in (source,fixture,patch,Path(__file__).resolve(),lock,simple/'include/libsimple/lock.h')}
    before=source.read_text();after=patch_xnu_wait_state({'src/thread.c':before})['src/thread.c']
    pin=subprocess.check_output(['git','-C',str(args.baseline),'rev-parse','HEAD'],text=True).strip()
    original=subprocess.check_output(['git','-C',str(args.baseline),'show',pin+':duct-tape/src/thread.c'],text=True)
    bounds=[('void dtape_thread_entering(', 'void dtape_thread_exiting('),('void dtape_thread_exiting(', 'void dtape_thread_set_handles(')]
    for start,end in bounds:
        if method(before,start,end)!=method(original,start,end):raise ValueError('selected dispatcher differs from pin')
    records=[];results={}
    def run(command):
        r=subprocess.run(command,capture_output=True,text=True,timeout=30,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=1'})
        records.append(dict(command=command,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr));(out/'commands.json').write_text(json.dumps(records,indent=2)+'\n');r.check_returncode();return r
    for variant,flags in [('native',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all'])]:
        sub=out/variant;sub.mkdir()
        obj=sub/'libsimple-lock.o';run(['clang','-std=gnu11','-pthread','-DLIBSIMPLE_LINUX=1',*flags,'-I'+str(simple/'include'),'-c',str(lock),'-o',str(obj)])
        for label,body in [('before',before),('after',after)]:
            text=fixture.read_text()
            slots={'ENTER':method(body,*bounds[0]),'EXIT':method(body,*bounds[1]),
                'MARK':method(body,'wait_result_t thread_mark_wait_locked(', 'kern_return_t thread_terminate('),
                'BLOCK':method(body,'wait_result_t thread_block_parameter(', 'wait_result_t thread_block('),
                'UNBLOCK':method(body,'boolean_t thread_unblock(', '// thread locked\nkern_return_t thread_go('),
                'SIGEXC':method(body,'void dtape_thread_sigexc_enter(', 'void dtape_thread_sigexc_enter2(')}
            for key,value in slots.items():text=text.replace('@'+key+'@',value)
            generated=sub/(label+'.c');generated.write_text(text);binary=sub/label
            command=['clang','-std=gnu11','-pthread','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-misleading-indentation','-Wno-unused-function',*flags]
            if label=='before':command+=['-DBEFORE_XNU_WAIT_STATE']
            run(command+['-I'+str(simple/'include'),str(generated),str(obj),'-o',str(binary)])
            results[variant+'-'+label]=json.loads(run([str(binary)]).stdout)
    if any(digest(Path(path))!=value for path,value in hashes.items()):raise ValueError('input mutated')
    (out/'generated-thread.c').write_text(after)
    report=dict(passed=True,scope=__doc__,input_sha256=hashes,inputs_unchanged=True,pinned_original=dict(repository=str(args.baseline.resolve()),commit=pin,dispatcher_methods_identical=True),results=results,
        model_boundary=['Exact selected C dispatcher, thread_mark_wait_locked, thread_block_parameter, thread_unblock and dtape_thread_sigexc_enter methods.',
            'Actual native libsimple gate; scheduler suspend/resume modeled by pthread condition variable, thread interlock by pthread mutex.',
            'Nested C-mutex-like dispatch before thread_block is modeled directly; waitq insertion/pull and timer operations are modeled.',
            'No continuation callback ownership or lifetime execution, no generic foreign wake reason tagging, no whole-runtime assertion.'],commands=records)
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(dict(passed=True,results=results)))
if __name__=='__main__':main()
