import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from cmake_declaration_inventory import parse
import cmake_command_effect_inventory as effects


def report_for(text, name='CMakeLists.txt'):
    commands = parse(text, name)
    return dict(upstream_commit='1' * 40, counts=dict(declarations=len(commands)), files=[dict(
        path=name, repository_path='', source_path=name, commit='1' * 40,
        object_id=hashlib.sha1(b'blob ' + str(len(text.encode())).encode() + b'\0' + text.encode()).hexdigest(),
        sha256=hashlib.sha256(text.encode()).hexdigest(), size=len(text.encode()), mode='100644',
        parse_status='valid', declarations=commands)])


class CatalogueTests(unittest.TestCase):
    def test_direct_effects_inside_definitions_keep_context_without_propagating_wrappers(self):
        report = report_for('''#[=[ exec_program(fake) ]=]
function(wrapper name)
 if(OPT)
  exec_program(tool ARGS "file(WRITE fake)")
  file(WRITE same.c "${value}")
  file(APPEND same.c tail)
  file(${MODE} unknown)
  try_run(RUN COMPILE dir source.c)
  add_custom_target(target DEPENDS dependency)
 endif()
endfunction()
wrapper(item)
''')
        result = effects.catalogue(report, {'fixture': 'a' * 64})
        rows = result['declarations']
        self.assertEqual([r['command'] for r in rows], ['exec_program', 'file', 'file', 'file', 'try_run', 'add_custom_target'])
        self.assertEqual(rows[0]['role'], 'legacy-process-command-name')
        self.assertEqual([frame['command'] for frame in rows[0]['context']], ['function', 'if'])
        self.assertEqual(rows[1]['role'], 'filesystem-write-command-name')
        self.assertIn('same.c', rows[1]['raw_arguments'])
        self.assertIn('same.c', rows[2]['raw_arguments'])
        self.assertEqual(rows[3]['role'], 'file-subcommand-unresolved')
        self.assertIsNone(rows[3]['literal_file_subcommand'])
        self.assertEqual(rows[4]['role'], 'compiler-and-run-probe-command-name')
        self.assertEqual(rows[5]['role'], 'custom-target-command-name')
        self.assertIn('wrapper', result['counts']['uncatalogued_by_command'])
        self.assertEqual(result['counts']['catalogued_declarations'] + result['counts']['uncatalogued_declarations'],
                         result['counts']['input_declarations'])
        self.assertTrue(all(r['execution_reachability'] == 'unknown' and not r['commands_executed'] for r in rows))
        for key, value in result['completion'].items():
            self.assertFalse(value, key)
        for row in rows:
            self.assertEqual(row['sha256'], report['files'][0]['sha256'])
            self.assertEqual(row['object_id'], report['files'][0]['object_id'])

    def test_literal_file_subcommands_quoted_bracket_and_dynamic(self):
        source = 'file("GENERATE" OUTPUT output CONTENT data)\nfile([=[\nREAD]=] input data)\nfile("${MODE}" path)\nfile(UNREVIEWED path)\n'
        rows = effects.catalogue(report_for(source), {})['declarations']
        self.assertEqual([r['role'] for r in rows], ['filesystem-write-command-name', 'filesystem-read-command-name',
                                                  'file-subcommand-unresolved', 'file-subcommand-unresolved'])
        self.assertEqual(rows[0]['literal_file_subcommand'], 'GENERATE')
        self.assertEqual(rows[1]['literal_file_subcommand'], 'READ')
        self.assertIsNone(rows[2]['literal_file_subcommand'])
        self.assertEqual(rows[3]['literal_file_subcommand'], 'UNREVIEWED')
        self.assertIsNone(effects.literal_word(dict(kind='quoted', raw='"WR\\ITE"')))

    def test_templates_are_not_treated_as_executed_scripts(self):
        result = effects.catalogue(report_for('exec_program("@CMAKE_COMMAND@" ARGS "@ARGUMENTS@")',
                                               'uninstall.cmake.in'), {})
        row = result['declarations'][0]
        self.assertEqual(row['template_context'], 'unexpanded-template-source')
        self.assertIn('@ARGUMENTS@', row['raw_arguments'])
        self.assertFalse(row['variables_expanded'])
        self.assertTrue(any('expansion' in text for text in row['unresolved']))

    def test_unparsed_inputs_and_unknown_custom_calls_are_preserved(self):
        report = report_for('custom_generator(a)')
        invalid = dict(report['files'][0], path='README.cmake', source_path='README.cmake',
                       parse_status='invalid-or-non-script', declarations=[], parse_error='prose input')
        report['files'].append(invalid)
        result = effects.catalogue(report, {})
        self.assertEqual(result['declarations'], [])
        self.assertEqual(result['counts']['uncatalogued_by_command'], {'custom_generator': 1})
        self.assertEqual(result['unparsed_inputs'][0]['parse_error'], 'prose input')
        self.assertFalse(result['completion']['handwritten_semantic_review_complete'])

    def test_symlink_inputs_are_unfollowed_references_without_effects(self):
        report = report_for('')
        report['files'][0].update(mode='120000', parse_status='symlink-not-followed',
                                  symlink_target='other.cmake')
        result = effects.catalogue(report, {})
        self.assertEqual(result['counts']['unparsed_inputs'], 0)
        self.assertEqual(result['counts']['unfollowed_symlink_inputs'], 1)
        self.assertEqual(result['declarations'], [])
        self.assertEqual(result['unfollowed_symlink_inputs'][0]['path'], 'CMakeLists.txt')

    def test_deterministic_sorting_without_target_or_implementation_counts(self):
        first = report_for('exec_program(a)', 'z.cmake')
        first['files'] += report_for('file(WRITE x y)', 'a.cmake')['files']
        first['counts']['declarations'] = 2
        reverse = copy.deepcopy(first)
        reverse['files'].reverse()
        result = effects.catalogue(first, {})
        self.assertEqual(result, effects.catalogue(reverse, {}))
        self.assertEqual([r['path'] for r in result['declarations']], ['a.cmake', 'z.cmake'])
        self.assertNotIn('targets', result)
        self.assertNotIn('with_execution_evidence', result['counts'])


class ValidatedInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'tools').mkdir()
        for name in ['cmake_declaration_inventory.py', 'cmake_inventory_evidence.py']:
            (self.root / 'tools' / name).write_bytes(Path(__file__).with_name(name).read_bytes())
        self.source = report_for('exec_program(tool)\nfile(WRITE output data)\n')
        row = self.source['files'][0]
        self.sp = dict(upstream_commit='1' * 40, entries=[{k: row[k] for k in
                     ('path', 'object_id', 'mode')} | dict(object_type='blob', category='build-cmake')])
        self.dep = dict(upstream_commit='1' * 40, dependencies=[])
        for name, data in [('migration-inventory.json', self.sp), ('dependency-inventory.json', self.dep)]:
            (self.root / name).write_text(json.dumps(data))
        self.source.update(schema_version=1, valid=True, parse_errors=[], unresolved=['No evaluation.'],
            coverage=dict(selected_exact_inputs_verified=True, all_commands_parsed=True,
                          handwritten_semantic_review_complete=False),
            inputs=dict(superproject_inventory_sha256=effects.digest(self.root / 'migration-inventory.json'),
                        dependency_inventory_sha256=effects.digest(self.root / 'dependency-inventory.json'),
                        tool_sha256=effects.digest(self.root / 'tools/cmake_declaration_inventory.py'),
                        repositories=[dict(path='', commit='1' * 40)]))
        self.source['counts'].update(files=1, repositories=1, parse_invalid_files=0,
                                    by_command=dict(exec_program=1, file=1), by_category=dict(other=2))
        self.path = self.root / 'declarations.json'
        self.path.write_text(json.dumps(self.source))

    def test_validated_hashes_and_provenance_then_drift_fail_closed(self):
        result = effects.build(self.root, self.path)
        self.assertEqual(result['inputs']['declarations.json'], effects.digest(self.path))
        self.assertEqual(result['counts']['catalogued_declarations'], 2)
        self.assertEqual(result, effects.build(self.root, self.path))
        (self.root / 'tools/cmake_declaration_inventory.py').write_text('drift')
        with self.assertRaisesRegex(ValueError, 'input drift'):
            effects.build(self.root, self.path)

    def test_mutated_source_provenance_and_accounting_fail_closed(self):
        original = copy.deepcopy(self.source)
        for mutate in [lambda r: r['files'][0].update(object_id='9' * 40),
                       lambda r: r['counts'].update(declarations=99),
                       lambda r: r['coverage'].update(handwritten_semantic_review_complete=True)]:
            report = copy.deepcopy(original)
            mutate(report)
            self.path.write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                effects.build(self.root, self.path)

    def test_cli_fresh_outputs_repeat_deterministically_and_preserve_existing(self):
        tool = Path(__file__).with_name('cmake_command_effect_inventory.py')
        dirs = [self.root / 'first', self.root / 'second']
        original = self.path.read_bytes()
        for directory in dirs:
            result = subprocess.run([sys.executable, str(tool), '--root', str(self.root),
                                     '--declarations', self.path.name, '--output-dir', str(directory)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        for name in ['declarations.json', 'SUMMARY.txt']:
            self.assertEqual((dirs[0] / name).read_bytes(), (dirs[1] / name).read_bytes())
        before = (dirs[0] / 'declarations.json').read_bytes()
        result = subprocess.run([sys.executable, str(tool), '--root', str(self.root),
                                 '--declarations', self.path.name, '--output-dir', str(dirs[0])],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((dirs[0] / 'declarations.json').read_bytes(), before)
        self.assertEqual(self.path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
