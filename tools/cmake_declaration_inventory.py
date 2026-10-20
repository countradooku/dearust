#!/usr/bin/env python3
"""Inventory exact-pinned CMake declarations without configuring or executing them.

This is a lexical inventory, not a CMake interpreter. No variable, list, generator
expression, include, macro, condition, glob or template expansion is performed.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile


class InventoryError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise InventoryError(f'duplicate JSON key: {key}')
            result[key] = value
        return result
    data = path.read_bytes()
    return json.loads(data, object_pairs_hook=pairs), digest(data)


def safe_path(path):
    if (not isinstance(path, str) or not path or path.startswith('/')
            or any(c in path for c in '\n\r\0')
            or any(p in ('', '.', '..') for p in path.split('/'))):
        raise InventoryError(f'invalid committed path: {path!r}')
    return path


def pin(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value):
        raise InventoryError(f'invalid exact Git pin: {value!r}')
    return value


def selected(row):
    path = row['path']
    return (row.get('category') == 'build-cmake' or PurePosixPath(path).name == 'CMakeLists.txt'
            or path.endswith(('.cmake', '.cmake.in')))


def sources(superproject, dependencies):
    upstream = pin(superproject['upstream_commit'])
    if dependencies['upstream_commit'] != upstream:
        raise InventoryError('inconsistent superproject/dependency pins')
    result, seen, dep_paths = [], set(), set()
    groups = [('', upstream, superproject['entries'])]
    for dependency in dependencies['dependencies']:
        prefix = safe_path(dependency['path'])
        if prefix in dep_paths:
            raise InventoryError(f'duplicate dependency: {prefix}')
        dep_paths.add(prefix)
        if dependency['availability'] != 'inventoried':
            raise InventoryError(f'dependency unavailable: {prefix}')
        groups.append((prefix, pin(dependency['commit']), dependency['entries']))
    group_leaves = {prefix: {row['path']: row for row in entries}
                    for prefix, _, entries in groups}
    for prefix, commit, _ in groups[1:]:
        parents = [p for p in group_leaves if p != prefix and (not p or prefix.startswith(p + '/'))]
        parent = max(parents, key=len)
        relative = prefix[len(parent) + 1:] if parent else prefix
        declared = group_leaves[parent].get(relative)
        if (not declared or declared.get('mode') != '160000'
                or declared.get('object_type') != 'commit' or declared.get('object_id') != commit):
            raise InventoryError(f'dependency gitlink provenance mismatch: {prefix}')
    for prefix, commit, entries in groups:
        local = set()
        for row in entries:
            path = safe_path(row['path'])
            if path in local:
                raise InventoryError(f'duplicate input: {prefix}/{path}')
            local.add(path)
            if not selected(row):
                continue
            full = f'{prefix}/{path}' if prefix else path
            if full in seen:
                raise InventoryError(f'duplicate source provenance: {full}')
            seen.add(full)
            if row['object_type'] != 'blob' or row['mode'] not in ('100644', '100755', '120000'):
                raise InventoryError(f'non-blob CMake input: {full}')
            result.append(dict(path=full, repository_path=prefix, commit=commit,
                               source_path=path, object_id=pin(row['object_id']), mode=row['mode']))
    return sorted(result, key=lambda row: row['path'])


def committed_blobs(repository, rows):
    """One exact commit:path cat-file request per input; never read working files."""
    requests = ''.join(f"{r['commit']}:{r['source_path']}\n" for r in rows).encode()
    run = subprocess.run(['git', '-C', str(repository), 'cat-file', '--batch'], input=requests,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if run.returncode:
        raise InventoryError(f'cat-file failed for {repository}: {run.stderr.decode(errors="replace")}')
    output, offset, result = run.stdout, 0, []
    for row in rows:
        end = output.find(b'\n', offset)
        if end < 0:
            raise InventoryError(f'missing cat-file header: {row["path"]}')
        header = output[offset:end].split()
        if len(header) != 3 or header[1] != b'blob' or not header[2].isdigit():
            raise InventoryError(f'unavailable/non-blob exact input: {row["path"]}')
        size = int(header[2])
        start = end + 1
        data = output[start:start + size]
        offset = start + size + 1
        actual = hashlib.sha1(b'blob ' + str(size).encode() + b'\0' + data).hexdigest()
        if (len(data) != size or output[offset - 1:offset] != b'\n'
                or header[0].decode() != row['object_id'] or actual != row['object_id']):
            raise InventoryError(f'blob provenance mismatch: {row["path"]}')
        result.append(data)
    if offset != len(output):
        raise InventoryError('unexpected cat-file trailing output')
    return result


BRACKET = re.compile(r'\[(=*)\[')
IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z_0-9]*')
DYNAMIC = re.compile(r'\$|@[^@\n]+@')
OPENERS = {'if': 'endif', 'foreach': 'endforeach', 'while': 'endwhile',
           'function': 'endfunction', 'macro': 'endmacro', 'block': 'endblock'}
CLOSERS = {value: key for key, value in OPENERS.items()}
TARGETS = {'add_library', 'add_executable', 'add_custom_target', 'add_darling_library',
           'add_darling_executable', 'add_framework', 'add_circular'}


def parse(text, path='<input>'):
    """Lex commands and balanced argument syntax; preserve unexpanded text exactly."""
    n, i, commands, stack = len(text), 0, [], []
    if text.startswith('\ufeff'):
        i = 1

    def fail(message, at):
        raise InventoryError(f'{path}:{text.count(chr(10), 0, at) + 1}: {message}')

    def bracket(at):
        match = BRACKET.match(text, at)
        if not match:
            return None
        closing = ']' + match[1] + ']'
        end = text.find(closing, match.end())
        if end < 0:
            fail('unterminated bracket argument/comment', at)
        return end + len(closing)

    def skip(at):
        while at < n:
            if text[at].isspace():
                at += 1
            elif text[at] == '#':
                ending = bracket(at + 1)
                if ending is not None:
                    at = ending
                else:
                    ending = text.find('\n', at)
                    at = n if ending < 0 else ending + 1
            else:
                break
        return at

    def quote(at):
        start = at
        at += 1
        while at < n:
            if text[at] == '\\':
                if at + 1 >= n:
                    fail('trailing quoted escape', at)
                at += 2
            elif text[at] == '"':
                return at + 1
            else:
                at += 1
        fail('unterminated quoted argument', start)

    while True:
        i = skip(i)
        if i == n:
            break
        start = i
        match = IDENTIFIER.match(text, i)
        if not match:
            fail('expected literal command identifier', i)
        name = match[0].lower()
        i = skip(match.end())
        if i == n or text[i] != '(':
            fail('expected command opening parenthesis', i)
        body = i + 1
        i += 1
        depth, tokens = 1, []
        while depth:
            i = skip(i)
            if i == n:
                fail('unterminated command', start)
            at = i
            if text[i] in '()':
                char = text[i]
                depth += 1 if char == '(' else -1
                i += 1
                if depth:
                    tokens.append(dict(kind='parenthesis', raw=char, offset=at))
                continue
            ending = bracket(i)
            if ending is not None:
                kind, i = 'bracket', ending
            elif text[i] == '"':
                kind, i = 'quoted', quote(i)
            else:
                kind = 'unquoted'
                while i < n and not text[i].isspace() and text[i] not in '()#':
                    if text[i] == '\\':
                        if i + 1 >= n:
                            fail('trailing unquoted escape', i)
                        i += 2
                    elif text[i] == '"':
                        kind, i = 'legacy-mixed-quoted', quote(i)
                    else:
                        i += 1
            tokens.append(dict(kind=kind, raw=text[at:i], offset=at))
        end = i
        if name in CLOSERS:
            if not stack or stack[-1]['command'] != CLOSERS[name]:
                fail(f'unmatched {name}', start)
            stack.pop()
        if name in ('else', 'elseif'):
            if not stack or stack[-1]['command'] != 'if':
                fail(f'unmatched {name}', start)
            if stack[-1]['branch'] == 'else':
                fail(f'{name} after else', start)
            stack[-1] = dict(stack[-1], branch=name, branch_arguments=text[body:end - 1])
        unresolved = ['declaration-only; not configured or executed']
        if stack:
            unresolved.append('enclosing condition, loop or definition is not evaluated')
        if any(DYNAMIC.search(t['raw']) for t in tokens):
            unresolved.append('variable, generator expression or template expansion is not evaluated')
        if any(t['kind'] == 'legacy-mixed-quoted' for t in tokens):
            unresolved.append('legacy mixed quoted argument retained without expansion')
        category = ('option' if name in ('option', 'cmake_dependent_option') else
                    'definition' if name in ('function', 'macro') else
                    'target' if name in TARGETS else
                    'custom-command' if name == 'add_custom_command' else
                    'configure' if name == 'configure_file' else
                    'file-generate' if name == 'file' and tokens and tokens[0]['raw'].upper() == 'GENERATE' else
                    'execute-process' if name == 'execute_process' else
                    'condition' if name in ('if', 'elseif', 'else', 'endif') else 'other')
        row = dict(command=name, category=category, offset=start, end_offset=end,
                   line=text.count('\n', 0, start) + 1,
                   column=start - text.rfind('\n', 0, start),
                   end_line=text.count('\n', 0, end) + 1,
                   raw=text[start:end], raw_arguments=text[body:end - 1], arguments=tokens,
                   context=[dict(frame) for frame in stack], unresolved=unresolved)
        if category in ('target', 'option', 'definition') and tokens:
            row['declared_name_raw'] = tokens[0]['raw']
        commands.append(row)
        if name in OPENERS:
            stack.append(dict(command=name, line=row['line'], raw_arguments=row['raw_arguments'],
                              branch='if' if name == 'if' else name))
    if stack:
        fail(f'unclosed {stack[-1]["command"]}', n)
    return commands


def inventory(root, baseline, super_path, dependency_path):
    superproject, super_hash = load(super_path)
    dependencies, dep_hash = load(dependency_path)
    rows = sources(superproject, dependencies)
    groups = {}
    for row in rows:
        groups.setdefault(row['repository_path'], []).append(row)
    files, parse_errors = [], []
    for prefix, inputs in sorted(groups.items()):
        repository = baseline / prefix
        for row, data in zip(inputs, committed_blobs(repository, inputs)):
            record = dict(row, sha256=digest(data), size=len(data))
            try:
                text = data.decode('utf-8')
            except UnicodeDecodeError as exc:
                error = f'{row["path"]}: non-UTF-8 input at byte {exc.start}; encoding unresolved'
                parse_errors.append(dict(path=row['path'], error=error))
                record.update(declarations=[], interpretation='unparsed exact input; no execution inference permitted',
                              parse_status='encoding-unresolved', parse_error=error,
                              review_required='Raw blob identity and hash are preserved; source encoding requires review.')
            else:
                if row['mode'] == '120000':
                    record.update(declarations=[], interpretation='committed symlink blob; not followed',
                                  parse_status='symlink-not-followed', symlink_target=text)
                else:
                    try:
                        declarations = parse(text, row['path'])
                    except InventoryError as exc:
                        error = str(exc)
                        parse_errors.append(dict(path=row['path'], error=error))
                        is_template = row['source_path'].endswith('.in')
                        record.update(declarations=[], interpretation='unparsed exact input; no execution inference permitted',
                                      parse_status='template-unresolved' if is_template else 'invalid-or-non-script',
                                      parse_error=error,
                                      review_required=('Template generation is unresolved; syntax cannot be inferred.' if is_template
                                                       else 'Selected filename/category does not establish valid CMake program content.'))
                    else:
                        record.update(declarations=declarations, interpretation='lexical declarations only',
                                      parse_status='valid')
            files.append(record)
    files.sort(key=lambda row: row['path'])
    categories = Counter(c['category'] for row in files for c in row['declarations'])
    commands = Counter(c['command'] for row in files for c in row['declarations'])
    return dict(schema_version=1, upstream_commit=superproject['upstream_commit'],
                valid=not parse_errors, parse_errors=parse_errors,
                coverage=dict(selected_exact_inputs_verified=True, all_commands_parsed=not parse_errors,
                              handwritten_semantic_review_complete=False),
                scope='Exact committed CMake declarations only; no configuration, execution, activation or migration completion.',
                locations='Offsets count decoded Unicode characters; line and column numbers are one-based.',
                unresolved=['Variables, lists, conditions, loops, definitions and generator expressions are not evaluated.',
                            'Include/subdirectory reachability, configured outputs, platform choices and target existence are not inferred.',
                            'Custom wrapper semantics and commands generated by templates or dynamic languages remain unresolved.',
                            'A declaration absent from the captured default graph is not evidence that its scope is absent.'],
                inputs=dict(superproject_inventory_sha256=super_hash, dependency_inventory_sha256=dep_hash,
                            tool_sha256=digest(Path(__file__).read_bytes()),
                            repositories=[dict(path='', commit=superproject['upstream_commit'])] +
                            sorted([dict(path=d['path'], commit=d['commit']) for d in dependencies['dependencies']],
                                   key=lambda d: d['path'])),
                counts=dict(files=len(files), parse_invalid_files=len(parse_errors),
                            repositories=1 + len(dependencies['dependencies']),
                            declarations=sum(commands.values()), by_category=dict(sorted(categories.items())),
                            by_command=dict(sorted(commands.items()))), files=files)


def summary(result):
    c = result['counts']
    return (f"Exact-pinned CMake declaration inventory\nUpstream: {result['upstream_commit']}\n"
            f"Repositories: {c['repositories']}\nFiles: {c['files']}\nDeclarations: {c['declarations']}\n"
            f"Parse-invalid files: {c['parse_invalid_files']}\nValid: {result['valid']}\n"
            + ''.join(f'{key}: {value}\n' for key, value in c['by_category'].items())
            + '\nDeclaration presence does not establish configured targets, execution, activation or completion.\n'
            + '\n'.join(result['unresolved']) + '\n'
            + ''.join(f"PARSE ERROR: {r['error']}\n" for r in result['parse_errors']))


def write_outputs(result, output_path, summary_path):
    """Publish complete fresh files only; refuse existing destinations."""
    paths = [Path(output_path), Path(summary_path)]
    if paths[0].absolute() == paths[1].absolute():
        raise InventoryError('output and summary must be distinct fresh paths')
    if any(path.exists() or path.is_symlink() for path in paths):
        raise InventoryError('refusing to replace an existing output')
    temporary, published = [], []
    try:
        for path, content in zip(paths, [json.dumps(result, indent=2, sort_keys=True) + '\n', summary(result)]):
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             prefix='.' + path.name + '.', delete=False) as handle:
                temporary.append(Path(handle.name))
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        for temp, path in zip(temporary, paths):
            os.link(temp, path)
            published.append((temp, path))
    except BaseException:
        for temp, path in published:
            try:
                actual, staged = path.lstat(), temp.stat()
            except FileNotFoundError:
                continue
            if (actual.st_dev, actual.st_ino) == (staged.st_dev, staged.st_ino):
                path.unlink()
        raise
    finally:
        for temp in temporary:
            temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--summary', type=Path, required=True)
    args = parser.parse_args()
    result = inventory(args.root, args.baseline, args.root / 'migration-inventory.json',
                       args.root / 'dependency-inventory.json')
    write_outputs(result, args.output, args.summary)
    if not result['valid']:
        raise SystemExit('CMake declaration inventory is invalid; see explicit parse errors in outputs')


if __name__ == '__main__':
    main()
