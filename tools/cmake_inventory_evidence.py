"""Validate pinned lexical build declarations without inferring configuration."""
from collections import Counter
import re

from cmake_declaration_inventory import sources


def checked_declarations(root, record, superproject, dependencies, observed, load, digest):
    path = root / record['path']
    if digest(path) != record['sha256']:
        raise ValueError('stale CMake declaration inventory')
    report = load(path)
    if report['schema_version'] != 1 or report['upstream_commit'] != superproject['upstream_commit']:
        raise ValueError('CMake declaration schema/pin mismatch')
    inputs = report['inputs']
    for key, filename in (
        ('superproject_inventory_sha256', 'migration-inventory.json'),
        ('dependency_inventory_sha256', 'dependency-inventory.json'),
        ('tool_sha256', 'tools/cmake_declaration_inventory.py'),
    ):
        if inputs[key] != digest(root / filename):
            raise ValueError('CMake declaration input drift: ' + filename)
    expected = {row['path']: row for row in sources(superproject, dependencies)}
    actual = {}
    categories, commands, literal_targets = Counter(), Counter(), {}
    unparsed, symlinks = [], []
    for row in report['files']:
        name = row['path']
        if name in actual:
            raise ValueError('duplicate CMake declaration file: ' + name)
        actual[name] = row
        if name not in expected or any(row[key] != value for key, value in expected[name].items()):
            raise ValueError('CMake declaration file provenance mismatch: ' + name)
        if row['mode'] == '120000':
            symlinks.append(name)
            if row['declarations']:
                raise ValueError('CMake symlink blob interpreted as commands')
        elif row['parse_status'] != 'valid':
            if row['parse_status'] not in ('invalid-or-non-script', 'template-unresolved', 'encoding-unresolved'):
                raise ValueError('unknown CMake parse status')
            unparsed.append(name)
            if row['declarations'] or not row.get('parse_error'):
                raise ValueError('invalid partial CMake parse claim')
        for declaration in row['declarations']:
            commands[declaration['command']] += 1
            categories[declaration['category']] += 1
            # Only plain unquoted literal names can be compared with this graph.
            # Missing names still do not establish optionality or reachability.
            if declaration['category'] != 'target':
                continue
            args = declaration['arguments']
            if args and args[0]['kind'] == 'unquoted' and re.fullmatch(r'[A-Za-z0-9_.:+/-]+', args[0]['raw']):
                literal_targets.setdefault(args[0]['raw'], []).append(
                    dict(path=name, line=declaration['line'], command=declaration['command']))
    if actual.keys() != expected.keys():
        raise ValueError('CMake declaration file coverage mismatch')
    repositories = [dict(path='', commit=superproject['upstream_commit'])] + sorted(
        [dict(path=row['path'], commit=row['commit']) for row in dependencies['dependencies']],
        key=lambda row: row['path'])
    if inputs['repositories'] != repositories:
        raise ValueError('CMake declaration repository provenance mismatch')
    counts = report['counts']
    if any(type(counts[key]) is not int or counts[key] < 0
           for key in ('files', 'repositories', 'declarations', 'parse_invalid_files')):
        raise ValueError('invalid CMake declaration count type')
    if (counts['files'] != len(actual) or counts['repositories'] != len(repositories)
            or counts['declarations'] != sum(commands.values())
            or counts['by_command'] != dict(sorted(commands.items()))
            or counts['by_category'] != dict(sorted(categories.items()))
            or counts['parse_invalid_files'] != len(unparsed)):
        raise ValueError('CMake declaration counts disagree with records')
    errors = [row['path'] for row in report['parse_errors']]
    if sorted(errors) != sorted(unparsed):
        raise ValueError('CMake declaration unresolved rows disagree')
    coverage = report['coverage']
    if (type(report['valid']) is not bool or report['valid'] != (not unparsed)
            or coverage['selected_exact_inputs_verified'] is not True
            or coverage['all_commands_parsed'] is not (not unparsed)
            or coverage['handwritten_semantic_review_complete'] is not False):
        raise ValueError('contradictory CMake declaration completeness claim')
    graph_names = {row['name'] for row in observed.values()}
    result = dict(path=record['path'], sha256=record['sha256'], counts=counts,
        provenance_verified=True, coverage=coverage, complete_configured_closure=False,
        configuration_evaluated=False, commands_executed=False,
        scope='Exact pinned lexical declarations; not configured targets, execution or migration completion.',
        unparsed_files=sorted(unparsed), unfollowed_symlink_blobs=sorted(symlinks),
        literal_target_names=len(literal_targets),
        literal_targets_absent_from_captured_graph=[dict(name=name, declarations=rows)
            for name, rows in sorted(literal_targets.items()) if name not in graph_names],
        unresolved=report['unresolved'])
    if record.get('role_review'):
        reference = record['role_review']
        role_path = root / reference['path']
        if digest(role_path) != reference['sha256']:
            raise ValueError('stale CMake input role review')
        review = load(role_path)
        if (review['schema_version'] != 1 or review['upstream_commit'] != report['upstream_commit']
                or review['inputs']['declaration_inventory_sha256'] != record['sha256']):
            raise ValueError('CMake input role review provenance mismatch')
        roles = {}
        for row in review['files']:
            name = row['path']
            if name in roles or name not in unparsed:
                raise ValueError('duplicate or unexpected CMake input role')
            if (any(row[key] != actual[name][key] for key in ('commit', 'object_id', 'sha256'))
                    or row['declaration_or_role_presence_establishes_execution'] is not False
                    or not row['role'] or not row['review_gate']):
                raise ValueError('contradictory CMake input role')
            for consumer in row['consumer_declarations']:
                selected = actual.get(consumer['path'])
                if selected is None or any(consumer[key] != selected[key]
                                          for key in ('commit', 'object_id', 'sha256')):
                    raise ValueError('CMake role consumer provenance mismatch')
            roles[name] = row
        role_counts = dict(sorted(Counter(row['role'] for row in roles.values()).items()))
        completion = review['completion']
        if (roles.keys() != set(unparsed) or review['reviewed_files'] != len(roles)
                or review['counts_by_role'] != role_counts
                or any(completion[key] is not False for key in ('configured_or_executed',
                    'reachability_resolved', 'handwritten_CMake_semantic_review_complete'))):
            raise ValueError('contradictory CMake role review completeness claim')
        result['input_role_review'] = dict(reference, all_unparsed_inputs_role_classified=True,
            counts_by_role=role_counts, reviewed_files=len(roles),
            configuration_evaluated=False, commands_executed=False,
            reachability_resolved=False, remaining_gates=review['remaining_gates'])
    return result
