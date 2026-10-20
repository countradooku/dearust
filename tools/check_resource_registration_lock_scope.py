#!/usr/bin/env python3
"""Exact Registry template with modeled acquisition/publication and real native migration.

No RPC, direct Checkin fault injection, closure ownership or whole-runtime claim.
Process/Thread source transforms are saved for owned-build validation separately.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from patch_resource_registration_lock_scope import patch_registration_lock_scope, preparation_pin_methods


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parent
    paths={name:args.source.resolve()/('src' if name.endswith('.cpp') else 'internal-include/darlingserver')/name for name in ('registry.hpp','process.hpp','thread.hpp','process.cpp','thread.cpp')}
    fixture=root/'fixtures/resource-registration-lock-scope-native.cpp';patch=root/'patch_resource_registration_lock_scope.py'
    pin_fixture=root/'fixtures/resource-registration-pin-cleanup-native.cpp.in';cleanup_header=root/'prepared-resource-cleanup.hpp'
    hashes={str(path):digest(path) for path in [*paths.values(),fixture,pin_fixture,cleanup_header,patch,Path(__file__).resolve()]}
    before={key:path.read_text() for key,path in paths.items()};after=patch_registration_lock_scope(before)
    for key,value in after.items():(out/('generated-'+key)).write_text(value)
    records=[];results={}
    def run(command):
        r=subprocess.run(command,capture_output=True,text=True,timeout=30,env={**os.environ,'ASAN_OPTIONS':'detect_leaks=1'})
        records.append(dict(command=command,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr));(out/'commands.json').write_text(json.dumps(records,indent=2)+'\n');r.check_returncode();return r
    for variant,flags in [('native',['-O2']),('sanitized',['-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all'])]:
        inc=out/variant/'include/darlingserver';inc.mkdir(parents=True)
        for name in ('message.hpp','process.hpp','thread.hpp'):(inc/name).write_text('#pragma once\nnamespace DarlingServer{class Process;class Thread;}\n')
        for label,files in [('before',before),('after',after)]:
            (inc/'registry.hpp').write_text(files['registry.hpp'])
            binary=out/variant/label
            command=['clang++','-std=c++17','-pthread','-Wall','-Wextra','-Werror',*flags]
            if label=='before':command+=['-DBEFORE_REGISTRATION_LOCK_SCOPE']
            run(command+['-I'+str(inc.parent),str(fixture),'-o',str(binary)])
            results[variant+'-'+label]=json.loads(run([str(binary)]).stdout)
        pin_source=pin_fixture.read_text().replace('@PROCESS_PIN_METHODS@', preparation_pin_methods('Process','_resourcePreparationParentTask')).replace('@THREAD_PIN_METHODS@', preparation_pin_methods('Thread','_resourcePreparationTask'))
        pin_path=out/variant/'pin-cleanup.cpp';pin_path.write_text(pin_source);pin_binary=out/variant/'pin-cleanup'
        run(['clang++','-std=c++17','-pthread','-Wall','-Wextra','-Werror',*flags,'-I'+str(root),str(pin_path),'-o',str(pin_binary)])
        results[variant+'-pin-cleanup']=json.loads(run([str(pin_binary)]).stdout)
    if any(digest(Path(path))!=value for path,value in hashes.items()):raise ValueError('input mutated')
    report=dict(passed=True,scope=__doc__,input_sha256=hashes,inputs_unchanged=True,results=results,
        model_boundary=[
            'Exact selected Registry template before/after; modeled Entry acquisition, publication and reference-release hooks.',
            'Real native ucontext migration across pthreads inside modeled foreign acquisition; no Registry native lock spans that migration.',
            'Concurrent same-NSID winner/loser, legacy insertion while prepared capacity outstanding, denied-allocation commit, publication failure and EID nonreuse.',
            'Process/Thread C acquisition and task generation validation require separate owned builds and ordinary guest execution.',
            'Exact preparation-pin finish/release methods and actual intrusive cleanup queue; final C task release and kernel execution identity modeled, no active closure lifetime work.',
            'ASan documents limited ucontext support; no comprehensive ucontext safety assertion.'],commands=records)
    (out/'verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(dict(passed=True,results=results)))
if __name__=='__main__':main()
