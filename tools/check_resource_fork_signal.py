#!/usr/bin/env python3
"""Exact fork-signal lock scope and C pin helpers, native ucontext migration.
Foreign semaphore operations/refcount lifecycle are modeled; no RPC, guest,
Checkin fault injection, closure-lifetime or whole-server safety claim.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from patch_resource_fork_signal import patch_fork_signal_cpp,patch_fork_signal_c,BEFORE,AFTER,DECLARATIONS,C_HELPERS


def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--semaphore-source',type=Path,required=True)
    p.add_argument('--baseline',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    tools=Path(__file__).resolve().parent
    paths=[a.source.resolve()/'src/process.cpp',a.semaphore_source.resolve(),tools/'patch_resource_fork_signal.py',Path(__file__).resolve(),tools/'fixtures/resource-fork-signal-native.cpp.in',tools/'fixtures/resource-fork-signal-semaphore-model.h',tools/'fixtures/resource-fork-signal-semaphore-model.c']
    hashes={str(x):digest(x) for x in paths}
    original=paths[0].read_text();patched=patch_fork_signal_cpp({'process.cpp':original})['process.cpp']
    c_original=a.semaphore_source.read_text();c_patched=patch_fork_signal_c({'src/semaphore.c':c_original})['src/semaphore.c']
    pin=subprocess.check_output(['git','-C',str(a.baseline),'rev-parse','HEAD'],text=True).strip()
    pinned_c=subprocess.check_output(['git','-C',str(a.baseline),'show',pin+':duct-tape/src/semaphore.c'],text=True)
    b=c_original.index('void dtape_semaphore_up(');e=c_original.index('dtape_semaphore_wait_result_t',b);up=c_original[b:e]
    b=pinned_c.index('void dtape_semaphore_up(');e=pinned_c.index('dtape_semaphore_wait_result_t',b)
    assert up==pinned_c[b:e]
    assert BEFORE in original and AFTER in patched
    (out/'generated-process.cpp').write_text(patched);(out/'generated-semaphore.c').write_text(c_patched)
    records=[];results={}
    def run(cmd):
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=60,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=1'})
        records.append(dict(command=cmd,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr));(out/'commands.json').write_text(json.dumps(records,indent=2)+'\n');r.check_returncode();return r
    for name,flags in [('native',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all'])]:
        target=out/name;target.mkdir()
        (target/'semaphore-model.h').write_text((tools/'fixtures/resource-fork-signal-semaphore-model.h').read_text())
        (target/'semaphore-model.c').write_text((tools/'fixtures/resource-fork-signal-semaphore-model.c').read_text()+'\n'+up+'\n'+C_HELPERS)
        obj=target/'semaphore-model.o'
        run(['clang','-std=c11','-pthread','-Wall','-Wextra','-Werror',*flags,'-c',str(target/'semaphore-model.c'),'-o',str(obj)])
        for label,body in [('before',BEFORE),('after',AFTER)]:
            text=(tools/'fixtures/resource-fork-signal-native.cpp.in').read_text().replace('@DECLARATIONS@',DECLARATIONS).replace('@SIGNAL@',body)
            cpp=target/(label+'.cpp');cpp.write_text(text);binary=target/label
            cmd=['clang++','-std=c++17','-pthread','-Wall','-Wextra','-Werror',*flags]
            if label=='before':cmd+=['-DBEFORE_FORK_SIGNAL_FIX']
            run(cmd+[str(cpp),str(obj),'-o',str(binary)])
            results[name+'-'+label]=json.loads(run([str(binary)]).stdout)
    if any(digest(x)!=h for x,h in hashes.items()):raise ValueError('inputs changed')
    report={'passed':True,'scope':__doc__,'input_sha256':hashes,'inputs_unchanged':True,'pinned_original':{'repository':str(a.baseline.resolve()),'commit':pin,'semaphore_blob':subprocess.check_output(['git','-C',str(a.baseline),'rev-parse',pin+':duct-tape/src/semaphore.c'],text=True).strip(),'up_method_identical':True},'results':results,'model_boundary':['Exact selected Process parent-lock/signal block before and after transform; listener body probes real shared_mutex availability.','Exact original dtape_semaphore_up and new C pin helpers; XNU structure, reference counting, deactivation and signal are modeled.','Actual native ucontext stack migrates between two native pthreads at modeled foreign suspension.','Original intentionally performs invalid native unlock, records stuck lock without blocking, then exits subprocess without reusing/destroying that rwlock.','Fixed runs32 migrations with16concurrent wrapper retirements; all pins release and listener mutex acquisitions succeed.','No guest/RPC or direct Checkin fault proof. ASan documents its limited swapcontext support; this does not establish comprehensive ucontext safety.'],'commands':records}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':True,'results':results}))
if __name__=='__main__':main()
