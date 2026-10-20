#!/usr/bin/env python3
"""Index the identified owned copy/snapshot proofs; never close resource gates.

Historical component reports remain immutable. Run their recorded commands to
rebuild evidence; this command verifies hashes and indexes the identified runs.
"""
import argparse
from pathlib import Path
import hashlib,json,re
root=Path(__file__).resolve().parents[1];work=root/'work/resource-copy-next'
if not __debug__:
 raise RuntimeError('Evidence assertions require Python without -O')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,default=root/'resource-copy-current-index.json')
args=parser.parse_args()
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path):return json.loads(Path(path).read_text())
def ref(path):return dict(path=str(Path(path).relative_to(root)),sha256=digest(path))
aggregate=load(work/'verification.json')
for name,value in aggregate['source_sha256'].items():assert digest(root/name)==value,name
for name,value in aggregate['evidence_sha256'].items():assert digest(work/name)==value,name
policy=load(root/'work/resource-policy-next/verification.json');assert policy['passed']
for name,value in policy['input_sha256'].items():assert digest(root/name)==value,name
runs=load(work/'guests-complete/run.json');cohorts=[];count=0
kernel=work/'matching-simd/libsystem_kernel-rust.dylib';kernel_hash=digest(kernel)
for group in runs:
 assert group['exit_code']==0
 report=Path(group['report']);data=load(report)
 for variant in data['variants']:
  assert variant['kernel_mappings'] and variant['staged_kernel_sha256']==kernel_hash
  assert variant['kernel_sha256']==kernel_hash
  for case in variant['results']:assert case['strict_pass']
  count+=len(variant['results'])
 cohorts.append(dict(group=group['group'],**ref(report)))
assert count==88
native=load(work/'simd-proof3/verification.json');assert native['inputs_unchanged']
for name,value in native['initial_sha256'].items():assert digest(root/name)==value,name
for record in native['records']:
 assert record['stdout'].startswith('386 exact-range cases')
 assert digest(record['command'][-1])==record['binary_sha256']
existing=load(work/'existing-guarded/verification.json');assert all(r['exit_code']==0 for r in existing['records'])
link=aggregate['link_preservation'];assert link['all_original_input_paths_and_hashes_unchanged'] and link['all_replacements_except_paths_unchanged']
assert digest(kernel)==link['new_kernel_sha256']
pinned=aggregate['pinned_link'];assert pinned['all_recorded_input_hashes_unchanged'] and pinned['public_symbol_sets_identical'] and pinned['import_symbol_sets_identical'] and pinned['i386_preserved_byte_identical']
workspace=work/'workspace-check.log';text=workspace.read_text();passed=sum(map(int,re.findall(r'test result: ok\. (\d+) passed;',text)));compilefail=text.count('compile fail ... ok');assert passed and 'test result: FAILED' not in text and not re.search(r'^error:',text,re.M)
python=work/'python-current.log';pytext=python.read_text();assert pytext.rstrip().endswith('OK');pycount=int(re.search(r'Ran (\d+) tests',pytext)[1])
darwin=work/'darwin-abi-check.log';darwin_text=darwin.read_text();assert 'test result: ok. 1 passed;' in darwin_text and 'FAILED' not in darwin_text
preservation={}
for name in ('preserved-candidates.json','stopped-wip-preservation4.json'):
 path=root/'work/resource-death-continuation'/name;values=load(path);values=values.get('sha256',values)
 assert all(digest(root/key)==value for key,value in values.items())
 preservation[name]=dict(**ref(path),count=len(values))
record=dict(schema_version=1,scope='Current resource copy loop and inactive snapshot foundation only; complete resource/multiworker/architecture/performance acceptance remains open.',implemented_checks_passed=True,resource_complete=False,resource_ownership_activated=False,blocked_checks_retried=False,performance_gate_passed=False,multiworker_gate_passed=False,
 source_sha256={**aggregate['source_sha256'],**{name:digest(root/name) for name in ('crates/dearust-process/src/resource_view.rs','crates/dearust-process/SAFETY-RESOURCES.txt')}},
 evidence=dict(copy_component=ref(work/'verification.json'),snapshot_component=ref(root/'work/resource-policy-next/verification.json'),historical_lifecycle=ref(root/'resource-lifecycle-current-index.json'),current_recorder=ref(Path(__file__).resolve())),
 guest=dict(executions=count,strict_passes=count,slots='Same owned server57/loader12/current SIMD kernel in both slots; not C versus Rust.',run=ref(work/'guests-complete/run.json'),cohorts=cohorts,server=ref(root/'work/resource-death-continuation/lockset57-single/server/linked/darlingserver'),loader=ref(root/'work/resources-complete/loader12/mldr'),kernel=ref(kernel)),
 links=dict(matching=link,pinned=pinned),
 native_comparison=aggregate['native'],stack_tail_followup=aggregate['stack_tail_followup'],guest_comparison=aggregate['guest']['summary'],
 performance_scope='Native exact C/previous/current cohorts and guest previous/current ABBA retain raw samples. C retains known output-conversion defects. Effects below1percent with overlapping distributions do not establish a material speedup; generic heap/crosspage CPU remains about68percent above exact C. No constant eligibility or memory advantage inferred.',
 darwin_abi=dict(**ref(darwin),command='CARGO_TARGET_DIR=work/resource-copy-next/workspace-target cargo test -p dearust-xtask --test runtime darwin_objects_preserve_exact_c_abi_and_staging -- --ignored --exact',exit_code=0,passed=1),
 workspace=dict(**ref(workspace),command='CARGO_TARGET_DIR=work/resource-copy-next/workspace-target cargo xtask check',exit_code=0,ordinary=passed-compilefail,compile_fail=compilefail),python=dict(**ref(python),command="python3 -m unittest discover -s tools -p 'test_*.py' -v",exit_code=0,count=pycount),preservation=preservation,
 open_gates=['Review-stopped intrusive C wait-admission work remains inactive and multiworker lockset remains failing; it was not retried or transferred.','Resource ownership/enforcement and inactive view integration, VM/credential/NPROC/fork/exec/cancellation/architecture/fault/shutdown gates remain unfinished.','Generic heap/crosspage copy CPU remains materially above C; this loop change does not resolve the required fresh mask-query cost.','Exact-pinned CMake declaration coverage is lexical; fifteen inputs remain unparsed and configuration/reachability/handwritten semantics are unverified.'])
args.output.write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
print(json.dumps(dict(guest_passes=count,rust=passed-compilefail,compile_fail=compilefail,python=pycount,resource_complete=False)))
