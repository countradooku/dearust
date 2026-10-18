#!/usr/bin/env python3
"""Validate reversed matching-kernel experiments and summarize stream, pathname or process-service CPU/resources.

Original C versus Rust inside the locally modified matching runtime, with the
same server and byte-identical guest binaries. Resource quantiles are medians
of per-process quantiles; memory counters use Linux KiB. No global speed claim.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from summarize_guest_copy import distribution, kernel_pair, reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('forward','reverse','resource-forward','resource-reverse','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--workload',choices=['stream','frontends','services','filesystem','resources'],default='stream')
    args = parser.parse_args()
    family=args.workload
    expected_rounds={'stream':5000,'frontends':200,'services':10000,'filesystem':200,'resources':10000}[family]
    if kernel_pair(args.forward,args.reverse) != kernel_pair(args.resource_forward,args.resource_reverse):
        raise ValueError('CPU and resource cohorts use different kernels')
    cpu = []
    for order,variant,case in reports(args.forward,args.reverse):
        if case['name'] != family+'-guest-bench.c':
            raise ValueError('unexpected CPU workload')
        samples = re.findall(family.upper()+r'_BENCH sample=(\d+) roundtrips=(\d+) cpu_us=(\d+) wall_us=(\d+)',case['stderr'])
        if len(samples)!=21 or {int(x[0]) for x in samples}!=set(range(21)):
            raise ValueError('expected21 complete CPU samples')
        for sample,rounds,c,wall in samples:
            if int(rounds)!=expected_rounds:
                raise ValueError('unexpected iteration count')
            cpu.append(dict(order=order,variant=variant,sample=int(sample),roundtrips=int(rounds),cpu_us=int(c),wall_us=int(wall)))
    resource = []
    fields={'rounds','cpu_us','first_round_us','p50_us','p95_us','p99_us','max_us','maxrss_linux_kib','initial_maxrss_linux_kib','rss_linux_kib','pss_linux_kib','private_dirty_linux_kib'}
    for order,variant,case in reports(args.resource_forward,args.resource_reverse):
        if not re.fullmatch(family+r'-resource-\d\d\.c',case['name']):
            raise ValueError('unexpected resource workload')
        lines=[line for line in case['stderr'].splitlines() if line.startswith(family.upper()+'_RESOURCE ')]
        if len(lines)!=1:
            raise ValueError('require exactly one resource record')
        values={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',lines[0])}
        if values.keys()!=fields or values['rounds']!=512:
            raise ValueError('incomplete resource record')
        if not values['p50_us']<=values['p95_us']<=values['p99_us']<=values['max_us']:
            raise ValueError('nonmonotonic quantiles')
        resource.append(dict(order=order,variant=variant,case=case['name'],values=values))
    summary={}
    for variant in ('baseline','rust'):
        samples=[r for r in cpu if r['variant']==variant]
        resources=[r['values'] for r in resource if r['variant']==variant]
        if len(samples)!=42 or len(resources)!=20:
            raise ValueError('require42 CPU samples and20 resource processes/variant')
        summary[variant]=dict(cpu_us_per_roundtrip=distribution([r['cpu_us']/r['roundtrips'] for r in samples]),wall_us_per_roundtrip=distribution([r['wall_us']/r['roundtrips'] for r in samples]),resources={key:distribution([r[key] for r in resources]) for key in sorted(fields-{'rounds'})})
    summary['cpu_change_percent']=100*(summary['rust']['cpu_us_per_roundtrip']['median']/summary['baseline']['cpu_us_per_roundtrip']['median']-1)
    inputs={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.forward,args.reverse,args.resource_forward,args.resource_reverse)}
    args.output.write_text(json.dumps(dict(scope=__doc__,summary=summary,cpu_records=cpu,resource_records=resource,inputs=inputs),indent=2)+'\n')
    print(json.dumps({key:({k:v['median'] for k,v in value['resources'].items()} if isinstance(value,dict) else value) for key,value in summary.items()},indent=2))


if __name__ == '__main__':
    main()
