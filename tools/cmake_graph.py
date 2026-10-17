#!/usr/bin/env python3
"""Capture the emitted CMake File API codemodel without reducing its semantics.

First create BUILD/.cmake/api/v1/query/codemodel-v2, then reconfigure upstream.
This captures an observed default build graph; it does not replace it with Cargo.
The timestamped reply index is omitted. Target and directory JSON objects remain
as emitted, with only absolute source/build roots replaced by $SOURCE/$BUILD.
"""
import argparse
from collections import Counter
import json
from pathlib import Path


def capture(build):
    reply = build / ".cmake/api/v1/reply"
    indexes = sorted(reply.glob("index-*.json"))
    if not indexes:
        raise ValueError("no File API reply; create codemodel-v2 query and configure first")
    index = json.loads(indexes[-1].read_text())
    models = [obj for obj in index["objects"] if obj["kind"] == "codemodel" and obj["version"]["major"] == 2]
    if len(models) != 1:
        raise ValueError("expected exactly one codemodel-v2 object")
    filename = models[0]["jsonFile"]
    if Path(filename).name != filename:
        raise ValueError("codemodel reference is not a basename")
    model = json.loads((reply / filename).read_text())
    source_root = model["paths"]["source"]
    build_root = model["paths"]["build"]
    objects = {filename: model}
    for config in model["configurations"]:
        for item in config.get("targets", []) + config.get("directories", []):
            ref = item.get("jsonFile")
            if ref:
                if Path(ref).name != ref:
                    raise ValueError("File API object reference is not a basename")
                objects[ref] = json.loads((reply / ref).read_text())
    def normalize(value):
        if isinstance(value, str):
            # Longer roots first: a build directory may have the source prefix.
            for root, token in sorted([(source_root,"$SOURCE"),(build_root,"$BUILD")], key=lambda pair:len(pair[0]), reverse=True):
                value = value.replace(root, token)
            return value
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            return {normalize(key):normalize(item) for key,item in value.items()}
        return value
    targets = [obj for obj in objects.values() if "type" in obj]
    return {"schema_version":1,
            "scope":"Observed upstream CMake File API codemodel-v2, complete emitted target/directory objects for this configuration; not a Cargo build replacement or full build verification",
            "normalization":"Only absolute source/build root strings replaced with $SOURCE/$BUILD; volatile reply index omitted; emitted JSON references, target names/IDs, flags, ordering and pass targets retained",
            "cmake":index["cmake"],
            "codemodel_file":filename,
            "counts":{"configurations":len(model["configurations"]),"targets":len(targets),
                      "by_type":dict(sorted(Counter(t["type"] for t in targets).items())),
                      "source_records":sum(len(t.get("sources", [])) for t in targets),
                      "generated_source_records":sum(bool(s.get("isGenerated")) for t in targets for s in t.get("sources", [])),
                      "target_dependency_edges":sum(len(t.get("dependencies", [])) for t in targets),
                      "targets_with_install_metadata":sum("install" in t for t in targets)},
            "objects":normalize(objects)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build",type=Path)
    parser.add_argument("--output",type=Path,default=Path("upstream-cmake-graph.json"))
    args=parser.parse_args()
    try:
        graph=capture(args.build)
        args.output.write_text(json.dumps(graph,indent=2,sort_keys=True)+"\n")
    except (ValueError,OSError,KeyError) as error:
        parser.exit(1,"CMake graph capture failed: "+str(error)+"\n")
    print(json.dumps(graph["counts"],sort_keys=True))


if __name__=="__main__":main()
