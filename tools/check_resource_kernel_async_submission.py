#!/usr/bin/env python3
"""Exact kernelAsync submission and setup methods with real native libsimple lock.
Registry/foreign handle allocation prefixes and runner dispatch are modeled.
The executing callback ownership/lifetime body remains unchanged and untested.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from patch_resource_kernel_async_submission import patch_kernel_async_submission


def method(s,start,end):
 b=s.index(start);return s[b:s.index(end,b)]
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--libsimple',type=Path,required=True);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
 tools=Path(__file__).resolve().parent;fixture=tools/'fixtures/resource-kernel-async-submission-native.cpp.in';paths=[a.source.resolve()/'src/thread.cpp',a.libsimple.resolve()/'src/lock.c',a.libsimple.resolve()/'include/libsimple/lock.h',fixture,Path(__file__).resolve(),tools/'patch_resource_kernel_async_submission.py']
 hashes={str(x):digest(x) for x in paths};before=paths[0].read_text();after=patch_kernel_async_submission({'thread.cpp':before})['thread.cpp']
 worker_start='static void kernelAsyncRunnerThreadWorker(';worker_end='void DarlingServer::Thread::kernelAsync('
 assert method(before,worker_start,worker_end)==method(after,worker_start,worker_end)
 pin=subprocess.check_output(['git','-C',str(a.baseline),'rev-parse','HEAD'],text=True).strip();original=subprocess.check_output(['git','-C',str(a.baseline),'show',pin+':src/thread.cpp'],text=True)
 # Selected prepared Registry differs from original registration; retain and
 # record both rather than pretending the complete method is pinned-identical.
 records=[];results={}
 def run(cmd):
  r=subprocess.run(cmd,capture_output=True,text=True,timeout=60,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=1'});records.append(dict(command=cmd,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr));(out/'commands.json').write_text(json.dumps(records,indent=2)+'\n');r.check_returncode();return r
 for variant,flags in [('native',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all'])]:
  target=out/variant;target.mkdir();inc=target/'include/darlingserver';inc.mkdir(parents=True)
  (inc/'workers.hpp').write_text('#pragma once\n#include <thread>\n#include <condition_variable>\n#include <mutex>\n#include <vector>\n')
  obj=target/'lock.o';run(['clang','-std=gnu11','-pthread','-DLIBSIMPLE_LINUX=1','-I'+str(a.libsimple.resolve()/'include'),*flags,'-c',str(a.libsimple.resolve()/'src/lock.c'),'-o',str(obj)])
  for label,s in [('before',before),('after',after)]:
   text=fixture.read_text();start=s.index('static std::queue<std::function<void()>> kernelAsyncRunnerQueue;') if label=='before' else s.index('// A staged list node')
   globals=s[start:s.index(worker_start,start)]
   slots={'GLOBALS':globals,'SETUP':method(s,'void DarlingServer::Thread::setupKernelThread(','void DarlingServer::Thread::startKernelThread('),'START':method(s,'void DarlingServer::Thread::startKernelThread(','void DarlingServer::Thread::impersonate('),'METHOD':method(s,'void DarlingServer::Thread::kernelAsync(','void DarlingServer::Thread::kernelSync(')}
   for k,v in slots.items():text=text.replace('@'+k+'@',v)
   cpp=target/(label+'.cpp');cpp.write_text(text);binary=target/label;cmd=['clang++','-std=c++17','-pthread','-Wall','-Wextra','-Werror','-Wno-unused-function',*flags]
   if label=='before':cmd+=['-DORIGINAL_SUBMISSION','-Wno-unused-variable','-Wno-unused-function']
   run(cmd+['-I'+str(inc.parent),'-I'+str(a.libsimple.resolve()/'include'),str(cpp),str(obj),'-o',str(binary)])
   calibrated=json.loads(run([str(binary),'first','100000']).stdout);cases=[]
   for budget in range(calibrated['attempts']+1):cases.append(dict(budget=budget,**json.loads(run([str(binary),'first',str(budget)]).stdout)))
   results[variant+'-'+label]={'calibrated':calibrated,'first_submission_cases':cases}
   if label=='after':
    warmcal=json.loads(run([str(binary),'warm','100000']).stdout);warm=[]
    for budget in range(warmcal['attempts']+1):warm.append(dict(budget=budget,**json.loads(run([str(binary),'warm',str(budget)]).stdout)))
    results[variant+'-'+label].update(optional_growth_cases=warm,concurrent=json.loads(run([str(binary),'concurrent']).stdout),null_init=json.loads(run([str(binary),'null-init']).stdout))
 if any(digest(p)!=h for p,h in hashes.items()):raise ValueError('input changed')
 (out/'generated-thread.cpp').write_text(after)
 report={'passed':True,'scope':__doc__,'input_sha256':hashes,'inputs_unchanged':True,'pinned_original':{'repository':str(a.baseline.resolve()),'commit':pin,'original_method_sha256':hashlib.sha256(method(original,'void DarlingServer::Thread::kernelAsync(','void DarlingServer::Thread::kernelSync(').encode()).hexdigest(),'selected_method_sha256':hashlib.sha256(method(before,'void DarlingServer::Thread::kernelAsync(','void DarlingServer::Thread::kernelSync(').encode()).hexdigest(),'selected_registry_preparation_differs_from_original':True},'results':results,'runner_execution_body_unchanged':True,'model_boundary':['Exact selected kernelAsync and setup/start methods; new exact queue/guard helpers.','Real selected C libsimple native lock; callback staging uses actual std::function/list/vector allocations.','Registry and foreign handle prefix modeled with three ordinary new steps; no actual dtape handle acquisition.','Model runner resume only counts publication; executing kernel runner loop and closure ownership/lifetime not exercised.','Child executables exit after recording state without global teardown; ref cleanup assertions cover modeled failed preparation, not whole-runtime shutdown.'],'commands':records}
 (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':True,'case_counts':{k:len(v['first_submission_cases']) for k,v in results.items()}}))
if __name__=='__main__':main()
