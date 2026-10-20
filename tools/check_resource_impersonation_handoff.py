#!/usr/bin/env python3
"""Exact impersonation/deferral methods, selected real WorkQueue, modeled foreign work.
No RPC, guest, closure-lifetime, or whole-Server claims.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from patch_resource_impersonation_handoff import patch_impersonation


def method(s,start,following):
    b=s.index(start);return s[b:s.index(following,b)]
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def once(s,a,b):
    if s.count(a)!=1:raise ValueError(a)
    return s.replace(a,b,1)
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--protocol-source',type=Path,default=Path('/home/radud/darling-lol/build/darling-src/src/external/darlingserver/include/darlingserver/rpc-supplement.h'));a=p.parse_args()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parent
    paths={'thread.cpp':a.source.resolve()/'src/thread.cpp','thread.hpp':a.source.resolve()/'internal-include/darlingserver/thread.hpp','workers.hpp':a.source.resolve()/'internal-include/darlingserver/workers.hpp','kqchan.cpp':a.source.resolve()/'src/kqchan.cpp','kqchan.hpp':a.source.resolve()/'internal-include/darlingserver/kqchan.hpp'}
    fixture=root/'fixtures/resource-impersonation-handoff-native.cpp.in';patch=root/'patch_resource_impersonation_handoff.py';caller_fixture=root/'fixtures/resource-impersonation-callers-native.inc'
    hashes={str(p):digest(p) for p in [*paths.values(),fixture,caller_fixture,a.protocol_source.resolve(),patch,Path(__file__).resolve(),root/'fixtures/resource-death-work-native.cpp']}
    before={k:p.read_text() for k,p in paths.items()};after=patch_impersonation(before)
    pin=subprocess.check_output(['git','-C',str(a.baseline),'rev-parse','HEAD'],text=True).strip()
    original=subprocess.check_output(['git','-C',str(a.baseline),'show',pin+':src/thread.cpp'],text=True)
    boundaries=[('void DarlingServer::Thread::impersonate(', 'std::shared_ptr<DarlingServer::Thread> DarlingServer::Thread::impersonatingThread()'),('void DarlingServer::Thread::_deferLocked(','void DarlingServer::Thread::defer('),('void DarlingServer::Thread::_undeferLocked(','void DarlingServer::Thread::undefer(')]
    for start,end in boundaries:
        if method(before['thread.cpp'],start,end)!=method(original,start,end):raise ValueError('selected method differs from pinned original: '+start)
    records=[];results={}
    def run(cmd):
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=60,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=1'})
        records.append({'command':cmd,'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr});(out/'commands.json').write_text(json.dumps(records,indent=2)+'\n');r.check_returncode();return r
    for variant,flags in [('native',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all'])]:
        inc=out/variant/'include/darlingserver';inc.mkdir(parents=True)
        (inc/'rpc-supplement.h').write_text(a.protocol_source.read_text())
        (inc/'config.hpp').write_text('#pragma once\nnamespace DarlingServer{struct Config{static constexpr bool singleThreadedWorkQueue=false;};}\n')
        (inc/'workers.hpp').write_text(once(before['workers.hpp'],'        void pushReserved(','''        bool testReservedIdle(ReservedWorkNode<WorkItem>& node) { std::unique_lock lock(_queueMutex); return !node.queued && !node.running; }
        void pushReserved('''))
        for label,files in [('before',before),('after',after)]:
            s=files['thread.cpp'];text=fixture.read_text()
            work=method(s,'void DarlingServer::Thread::doWork() {','void DarlingServer::Thread::suspend(')
            entry=work[work.index('\tif (_deferralState') if label=='before' else work.index('    if (_impersonationActive)'):work.index('\n\tif (_terminating)')]
            slots={'IMPERSONATE':method(s,*boundaries[0]),'DEFER':method(s,*boundaries[1]),'UNDEFER':method(s,*boundaries[2]),'HANDOFF':s[s.index('void DarlingServer::Thread::_handoffPendingWorkLocked() noexcept {'):], 'ABORT':method(s,'void DarlingServer::Thread::abortKernelHandles() noexcept {','void DarlingServer::Thread::_resourceCleanupRun('),'NOTIFY':method(s,'void DarlingServer::Thread::notifyDead() {','bool DarlingServer::Thread::isDead() const'),'ENTRY':entry}
            if label=='after':
                support,tests=caller_fixture.read_text().split('// @TESTS@')
                slots['CALLER_SUPPORT']=support
                slots['CALLER_TESTS']=tests.replace('@WAKE_WAITERS@','')
                slots['CALLERS']=method(files['kqchan.cpp'],'void DarlingServer::Kqchan::MachPort::_completePreparedMachRead(', 'bool DarlingServer::Kqchan::MachPort::_processPreparedTransportEvent(')
            else:
                slots.update(CALLER_SUPPORT='',CALLERS='',CALLER_TESTS='')
            for k,v in slots.items():text=text.replace('@'+k+'@',v)
            src=out/variant/(label+'.cpp');src.write_text(text);binary=out/variant/label
            cmd=['clang++','-std=c++17','-pthread','-Wall','-Wextra','-Werror','-Wno-unused-parameter',*flags]
            if label=='before':cmd+=['-DBEFORE_IMPERSONATION_FIX']
            run(cmd+['-I'+str(inc.parent),str(src),'-o',str(binary)])
            results[variant+'-'+label]=json.loads(run([str(binary)]).stdout)
        (inc/'config.hpp').write_text('#pragma once\nnamespace DarlingServer{struct Config{static constexpr bool singleThreadedWorkQueue=true;};}\n')
        (inc/'workers.hpp').write_text('extern void reservedTestDying() noexcept;\n'+once(before['workers.hpp'],'joining.swap(_workerThreads);','joining.swap(_workerThreads); reservedTestDying();'))
        binary=out/variant/'reserved-dispatch'
        run(['clang++','-std=c++17','-pthread','-Wall','-Wextra','-Werror',*flags,'-I'+str(inc.parent),str(root/'fixtures/resource-death-work-native.cpp'),'-o',str(binary)])
        run([str(binary)])
    if any(digest(p)!=h for p,h in hashes.items()):raise ValueError('input mutated')
    (out/'generated-kqchan.cpp').write_text(after['kqchan.cpp']);(out/'generated-kqchan.hpp').write_text(after['kqchan.hpp']);(out/'generated-thread.cpp').write_text(after['thread.cpp']);(out/'generated-thread.hpp').write_text(after['thread.hpp'])
    report={'passed':True,'scope':__doc__,'input_sha256':hashes,'inputs_unchanged':True,'original':{'repository':str(a.baseline.resolve()),'commit':pin,'thread_cpp_blob':subprocess.check_output(['git','-C',str(a.baseline),'rev-parse',pin+':src/thread.cpp'],text=True).strip(),'selected_impersonation_and_deferral_methods_identical':True},'client_protocol':{'repository':str((a.baseline.resolve().parent/'libkqueue')),'commit':subprocess.check_output(['git','-C',str(a.baseline.resolve().parent/'libkqueue'),'rev-parse','HEAD'],text=True).strip(),'machport_blob':subprocess.check_output(['git','-C',str(a.baseline.resolve().parent/'libkqueue'),'rev-parse','HEAD:src/linux/machport.c'],text=True).strip(),'error_semantics':'Read/modify reject any nonzero code; read 0xdead means no event; negative Linux ESRCH is -3. Client returns -1 but does not itself set errno from reply.'},'results':results,'model_boundary':['Foreign C work modeled by accepted-call consumption; no ucontext switching.','Exact selected impersonate, _deferLocked, _undeferLocked, doWork entry guards, notifyDead, abortKernelHandles and handoff helper.','Test-only cv wrapper observes blocked claimant; synchronization delegates to native condition_variable_any.','Actual selected WorkQueue plus full existing reserved-node regression.','Exact new caller methods and fixed completion methods send selected protocol structs over real SOCK_SEQPACKET socketpair. Foreign work and async runner body are modeled; no RPC.','Reverse impersonation wake routing remains inactive: shared C wait identity cannot disambiguate overlapping target/impersonator waits.'],'commands':records}
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':True,'results':results}))
if __name__=='__main__':main()
