#!/usr/bin/env python3
"""Catalogue direct command-name effects in pinned lexical CMake declarations.

Preparation only: never execute commands, expand variables or infer reachability.
Custom wrappers and dynamically selected command names are not interpreted.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from cmake_declaration_inventory import load
from cmake_inventory_evidence import checked_declarations


COMMAND_ROLES = {
    'execute_process': 'process-command-name',
    'exec_program': 'legacy-process-command-name',
    'try_compile': 'compiler-probe-command-name',
    'try_run': 'compiler-and-run-probe-command-name',
    'configure_file': 'configured-file-command-name',
    'configure_package_config_file': 'package-config-generator-command-name',
    'add_custom_command': 'build-rule-command-name',
    'add_custom_target': 'custom-target-command-name',
}
FILE_ROLES = {
    **{name: 'filesystem-write-command-name' for name in ('WRITE', 'APPEND', 'GENERATE', 'CONFIGURE')},
    **{name: 'filesystem-copy-command-name' for name in ('COPY', 'INSTALL')},
    **{name: 'filesystem-remove-command-name' for name in ('REMOVE', 'REMOVE_RECURSE')},
    **{name: 'filesystem-read-command-name' for name in ('READ', 'STRINGS', 'MD5', 'SHA1', 'SHA224', 'SHA256', 'SHA384', 'SHA512', 'SHA3_224', 'SHA3_256', 'SHA3_384', 'SHA3_512', 'SIZE', 'TIMESTAMP')},
    **{name: 'filesystem-enumeration-command-name' for name in ('GLOB', 'GLOB_RECURSE')},
    **{name: 'path-computation-command-name' for name in ('RELATIVE_PATH', 'TO_CMAKE_PATH', 'TO_NATIVE_PATH', 'REAL_PATH')},
    **{name: 'filesystem-metadata-command-name' for name in ('MAKE_DIRECTORY', 'RENAME', 'CREATE_LINK', 'TOUCH', 'TOUCH_NOCREATE', 'CHMOD', 'CHMOD_RECURSE', 'LOCK')},
    **{name: 'archive-command-name' for name in ('ARCHIVE_CREATE', 'ARCHIVE_EXTRACT')},
    **{name: 'network-transfer-command-name' for name in ('DOWNLOAD', 'UPLOAD')},
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def literal_word(token):
    """Recognize only a literal ASCII word; never unescape or expand a token."""
    raw = token['raw']
    if token['kind'] == 'quoted':
        raw = raw[1:-1]
    elif token['kind'] == 'bracket':
        match = re.fullmatch(r'\[(=*)\[(.*?)\]\1\]', raw, re.DOTALL)
        if not match:
            return None
        raw = match[2]
        if raw.startswith('\r\n'):
            raw = raw[2:]
        elif raw.startswith('\n'):
            raw = raw[1:]
    elif token['kind'] != 'unquoted':
        return None
    return raw.upper() if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', raw) else None


def catalogue(report, inputs):
    records, unparsed, symlinks = [], [], []
    roles, commands, uncatalogued = Counter(), Counter(), Counter()
    for file in report['files']:
        if file.get('mode') == '120000':
            symlinks.append(dict(path=file['path'], commit=file['commit'], object_id=file['object_id'], sha256=file['sha256']))
        elif file.get('parse_status') != 'valid':
            unparsed.append(dict(path=file['path'], commit=file['commit'], object_id=file['object_id'],
                                 sha256=file['sha256'], parse_status=file.get('parse_status'),
                                 parse_error=file.get('parse_error')))
        for declaration in file['declarations']:
            name = declaration['command']
            subcommand = None
            if name == 'file':
                args = declaration['arguments']
                subcommand = literal_word(args[0]) if args else None
                role = FILE_ROLES.get(subcommand, 'file-subcommand-unresolved')
            else:
                role = COMMAND_ROLES.get(name)
            if role is None:
                uncatalogued[name] += 1
                continue
            record = {key: file[key] for key in ('path', 'repository_path', 'source_path', 'commit', 'object_id', 'sha256')}
            record.update({key: declaration[key] for key in ('command', 'line', 'end_line', 'column', 'offset',
                                                             'end_offset', 'raw', 'raw_arguments', 'arguments', 'context', 'unresolved')})
            record.update(role=role, lexical_category=declaration['category'],
                          template_context='unexpanded-template-source' if file['source_path'].endswith('.in') else 'no-template-filename-inferred',
                          execution_reachability='unknown', commands_executed=False,
                          variables_expanded=False, configured_behavior_resolved=False)
            if name == 'file':
                record['literal_file_subcommand'] = subcommand
                if role == 'file-subcommand-unresolved':
                    record['unresolved'] = record['unresolved'] + ['file subcommand is dynamic, absent or outside this catalogue; no effect inferred']
            records.append(record)
            roles[role] += 1
            commands[name] += 1
    records.sort(key=lambda row: (row['path'], row['offset']))
    return dict(schema_version=1, upstream_commit=report['upstream_commit'], inputs=inputs,
                scope='Static direct command-name role catalogue only; no CMake interpretation, execution, target addition or implementation accounting.',
                completion=dict(commands_executed=False, variables_expanded=False, reachability_resolved=False,
                                configured_behavior_resolved=False, handwritten_semantic_review_complete=False,
                                resource_gate_complete=False, ownership_activated=False),
                interpretation=['Roles identify source command spellings, not actual invocations or resolved effects. Builtin or module names can be shadowed; definitions and conditions are retained without evaluating command resolution.',
                                'Custom command/target declarations do not establish that they contain a process command or execute during any build.',
                                'Compiler probe declarations do not establish compiler execution, test execution, successful detection, or a platform result.',
                                'File arguments, output paths, shared outputs, templates and generator expressions remain unexpanded.',
                                'Uncatalogued commands, custom wrapper/transitive effects and the original unparsed inputs remain open semantic review scope.',
                                'This preparation adds no configured target, implementation, execution, activation, replacement or retirement counts.'],
                supported_command_names=sorted(COMMAND_ROLES), supported_literal_file_subcommands=dict(sorted(FILE_ROLES.items())),
                counts=dict(input_files=len(report['files']), input_declarations=report['counts']['declarations'],
                            catalogued_declarations=len(records), unparsed_inputs=len(unparsed),
                            unfollowed_symlink_inputs=len(symlinks),
                            by_role=dict(sorted(roles.items())), by_command=dict(sorted(commands.items())),
                            uncatalogued_declarations=sum(uncatalogued.values()),
                            uncatalogued_by_command=dict(sorted(uncatalogued.items()))),
                unparsed_inputs=sorted(unparsed, key=lambda row: row['path']),
                unfollowed_symlink_inputs=sorted(symlinks, key=lambda row: row['path']), declarations=records)


def build(root, declaration_path):
    relative = declaration_path.relative_to(root)
    report, report_hash = load(declaration_path)
    superproject, _ = load(root / 'migration-inventory.json')
    dependencies, _ = load(root / 'dependency-inventory.json')
    checked_declarations(root, dict(path=str(relative), sha256=report_hash), superproject,
                         dependencies, {}, lambda path: load(path)[0], digest)
    paths = [str(relative), 'migration-inventory.json', 'dependency-inventory.json',
             'tools/cmake_declaration_inventory.py', 'tools/cmake_inventory_evidence.py']
    inputs = {path: digest(root / path) for path in paths}
    inputs['tools/cmake_command_effect_inventory.py'] = digest(Path(__file__))
    return catalogue(report, inputs)


def readable(report):
    c = report['counts']
    return ('Pinned CMake direct command-name role catalogue\n'
            f"Input declarations: {c['input_declarations']}\nCatalogued: {c['catalogued_declarations']}\n"
            f"Uncatalogued: {c['uncatalogued_declarations']}\nUnparsed inputs: {c['unparsed_inputs']}\n"
            + ''.join(f'{key}: {value}\n' for key, value in c['by_role'].items())
            + '\nNo command execution, variable expansion, resolved reachability or implementation completion.\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--declarations', type=Path, default=Path('cmake-declarations.json'))
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    declaration_path = (root / args.declarations).resolve()
    report = build(root, declaration_path)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    (args.output_dir / 'declarations.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    (args.output_dir / 'SUMMARY.txt').write_text(readable(report))


if __name__ == '__main__':
    main()
