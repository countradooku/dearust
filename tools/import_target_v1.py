#!/usr/bin/env python3
"""Extract an immutable historical v1 annotation snapshot for a reviewed v2 ledger.

Explicitly authored scoped artifact/evidence bindings are still required; this
importer cannot infer execution, production activation or retirement from v1.
"""
import argparse
import json
from target_inventory import digest, import_v1, load


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input')
    parser.add_argument('output')
    args = parser.parse_args()
    from pathlib import Path
    source = Path(args.input)
    result = import_v1(load(source))
    result['input_sha256'] = digest(source)
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    main()
