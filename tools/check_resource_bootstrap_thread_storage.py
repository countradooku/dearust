#!/usr/bin/env python3
"""Exact Thread publication/storage methods with modeled active bootstrap C helper.

No RPC, guest exec, Checkin faults, or closure lifetime work. Foreign C creation
and Process generation are modeled; owned builds validate real integration.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from patch_resource_registration_lock_scope import patch_registration_lock_scope
from patch_resource_bootstrap_thread_storage import patch_bootstrap_thread_storage


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def method(s,start,end):
    b=s.index(start);return s[b:s.index(end,b)]
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);tools=Path(__file__).resolve().parent
    paths={name:a.source.resolve()/('src' if name.endswith('.cpp') else 'internal-include/darlingserver')/name for name in ('registry.hpp','process.hpp','thread.hpp','process.cpp','thread.cpp')}
    fixture=tools/'fixtures/resource-bootstrap-thread-storage-native.cpp.in'
    hashes={str(path):digest(path) for path in [*paths.values(),fixture,Path(__file__).resolve(),tools/'patch_resource_bootstrap_thread_storage.py',tools/'patch_resource_registration_lock_scope.py']}
    before=patch_registration_lock_scope({key:path.read_text() for key,path in paths.items()});after=patch_bootstrap_thread_storage(before)
    for key,value in after.items():(out/('generated-'+key)).write_text(value)
    records=[];results={}
    def run(cmd):
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=30,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=1'})
        records.append(dict(command=cmd,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr));(out/'commands.json').write_text(json.dumps(records,indent=2)+'\n');r.check_returncode();return r
    for variant,flags in [('native',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all'])]:
        sub=out/variant;sub.mkdir()
        for label,files in [('before',before),('after',after)]:
            source=files['thread.cpp'];handles=method(source,'void DarlingServer::Thread::prepareKernelHandles() {','void DarlingServer::Thread::finishPreparation()')
            boot=handles[handles.index('    if (bootstrap) {'):handles.index('    requireResourceCleanupReady();')]
            slots={'BOOTSTRAP':boot,'STAGE':'' if label=='before' else method(source,'void DarlingServer::Thread::stageBootstrapPublication() {','void DarlingServer::Thread::preparePublication() {'),
                'PREPARE':method(source,'void DarlingServer::Thread::preparePublication() {','void DarlingServer::Thread::commitPublication()'),
                'COMMIT':method(source,'void DarlingServer::Thread::commitPublication() noexcept {','void DarlingServer::Thread::abortPublication()'),
                'ABORT':method(source,'void DarlingServer::Thread::abortPublication() noexcept {','void DarlingServer::Thread::registerWithProcess()') if 'void DarlingServer::Thread::abortPublication() noexcept {' in source else method(source,'void DarlingServer::Thread::abortPublication() {','void DarlingServer::Thread::registerWithProcess()'),
                'REPLACEMENT_CAPACITY':'' if label=='before' else method(files['process.cpp'],'        if (_preparedThreadsInFlight >= replacementThreads.max_size())','        listeners.reserve(').replace('_preparedThreadsInFlight','process->_preparedThreadsInFlight')}
            text=fixture.read_text()
            for key,value in slots.items():text=text.replace('@'+key+'@',value)
            generated=sub/(label+'.cpp');generated.write_text(text);binary=sub/label
            cmd=['clang++','-std=c++17','-pthread','-Wall','-Wextra','-Werror',*flags]
            if label=='before':cmd+=['-DBEFORE_BOOTSTRAP_STORAGE']
            run(cmd+[str(generated),'-o',str(binary)]);results[variant+'-'+label]=json.loads(run([str(binary)]).stdout)
    if any(digest(Path(path))!=value for path,value in hashes.items()):raise ValueError('input mutated')
    report=dict(passed=True,scope=__doc__,input_sha256=hashes,inputs_unchanged=True,results=results,model_boundary=['Exact private-node stage, Thread prepare/commit/abort publication, and bootstrap acquisition branch; C handle creation and kernel Process task identity modeled.','Exact Process replacement-map capacity reservation fragment, no guest exec or RPC.','Intervening ordinary insertions and map swap, denied-allocation commit/rollback, active helper and no held Process lock in foreign stage.'],commands=records)
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(dict(passed=True,results=results)))
if __name__=='__main__':main()
