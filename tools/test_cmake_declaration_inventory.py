import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import cmake_declaration_inventory as inventory


class LexerTests(unittest.TestCase):
    def test_comments_quotes_brackets_escapes_nested_conditions(self):
        text = '''#[=[ add_executable(fake a)\n fake(] ignored ) ]=]
option(OPTION_A "a # comment ) add_library(fake)" ON)
if(OPTION_A AND (NOT OTHER))
  function(make name)
    add_custom_command(OUTPUT "${name}.c" [==[shared;file#).c]==]
      COMMAND tool "add_library(fake)" a\\ b \\\n next BYPRODUCTS shared.c)
  endfunction()
elseif(OTHER)
  add_library(other STATIC x.c)
else()
  file(GENERATE OUTPUT "${out}" CONTENT [=[execute_process(fake)]=])
endif()
'''
        rows = inventory.parse(text)
        self.assertEqual([r['command'] for r in rows], ['option', 'if', 'function', 'add_custom_command',
                         'endfunction', 'elseif', 'add_library', 'else', 'file', 'endif'])
        custom = rows[3]
        self.assertEqual([x['command'] for x in custom['context']], ['if', 'function'])
        self.assertIn('BYPRODUCTS shared.c', custom['raw_arguments'])
        self.assertTrue(any(t['raw'] == '[==[shared;file#).c]==]' for t in custom['arguments']))
        self.assertEqual(rows[6]['context'][0]['branch'], 'elseif')
        self.assertEqual(rows[8]['category'], 'file-generate')
        for row in rows:
            self.assertEqual(text[row['offset']:row['end_offset']], row['raw'])
            self.assertEqual(text.count('\n', 0, row['offset']) + 1, row['line'])

    def test_dynamic_shared_outputs_remain_raw(self):
        rows = inventory.parse('add_custom_command(OUTPUT same.c ${name} COMMAND tool)\n'
                               'add_custom_command(OUTPUT same.c COMMAND second)\n'
                               'if(OPTIONAL)\nadd_executable(print_wrapped_elf x.c)\nendif()')
        self.assertEqual(sum(r['category'] == 'custom-command' for r in rows), 2)
        self.assertIn('${name}', rows[0]['raw_arguments'])
        self.assertTrue(any('expansion' in u for u in rows[0]['unresolved']))
        self.assertEqual(rows[3]['declared_name_raw'], 'print_wrapped_elf')
        self.assertTrue(rows[3]['context'])

    def test_invalid_fails_closed(self):
        for text in ['add_library(a', 'set(a "bad)', 'set(a [=[bad)', 'if(A)\nendfunction()',
                     'if(A)', 'endif()', 'if(A)\nelse()\nelseif(B)\nendif()', '@COMMAND@(x)']:
            with self.subTest(text=text), self.assertRaises(inventory.InventoryError):
                inventory.parse(text)

    def test_legacy_embedded_quotes_and_escaped_parenthesis(self):
        row = inventory.parse('set(X -Dname="a b" a\\)b)')[0]
        self.assertEqual(row['arguments'][1]['kind'], 'legacy-mixed-quoted')
        self.assertEqual(row['arguments'][2]['raw'], 'a\\)b')


class GitFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.repo = self.path / 'baseline'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        (self.repo / 'CMakeLists.txt').write_text('option(OPTIONAL "optional" OFF)\n'
                                              'if(OPTIONAL)\nadd_executable(optional_tool a.c)\nendif()\n')
        (self.repo / 'recipe.cmake.in').write_text('configure_file(@INPUT@ ${OUTPUT})\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.commit = self.git('rev-parse', 'HEAD').strip()
        self.rows = [dict(path=p, object_type='blob', object_id=self.git('rev-parse', f'{self.commit}:{p}').strip(),
                          category='build-cmake', mode='100644') for p in ('CMakeLists.txt', 'recipe.cmake.in')]
        self.super = dict(upstream_commit=self.commit, entries=self.rows)
        self.dep = dict(upstream_commit=self.commit, dependencies=[])

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout

    def test_committed_blob_only_deterministic_and_optional_absent_graph(self):
        root = self.path / 'inputs'
        root.mkdir()
        sp, dp = root / 'migration-inventory.json', root / 'dependency-inventory.json'
        sp.write_text(json.dumps(self.super))
        dp.write_text(json.dumps(self.dep))
        first = inventory.inventory(root, self.repo, sp, dp)
        (self.repo / 'CMakeLists.txt').write_text('THIS IS DIRTY INVALID WORKING SOURCE')
        (self.repo / 'recipe.cmake.in').unlink()
        second = inventory.inventory(root, self.repo, sp, dp)
        self.assertEqual(first, second)
        self.assertEqual(first['counts']['files'], 2)
        self.assertEqual(first['files'][0]['commit'], self.commit)
        self.assertEqual(first['files'][0]['declarations'][2]['declared_name_raw'], 'optional_tool')
        self.assertEqual(first['files'][0]['sha256'], hashlib.sha256(self.git('show', f'{self.commit}:CMakeLists.txt').encode()).hexdigest())
        self.assertIn('target existence', first['unresolved'][1])

    def test_duplicate_input_rejected(self):
        self.super['entries'].append(dict(self.rows[0]))
        with self.assertRaisesRegex(inventory.InventoryError, 'duplicate input'):
            inventory.sources(self.super, self.dep)

    def test_mismatch_and_missing_blob_rejected(self):
        rows = inventory.sources(self.super, self.dep)
        rows[0]['object_id'] = '0' * 40
        with self.assertRaisesRegex(inventory.InventoryError, 'provenance mismatch'):
            inventory.committed_blobs(self.repo, rows)
        rows[0]['source_path'] = 'not-committed.cmake'
        with self.assertRaisesRegex(inventory.InventoryError, 'unavailable'):
            inventory.committed_blobs(self.repo, rows)

    def test_inconsistent_and_duplicate_dependency_provenance_rejected(self):
        dep = dict(self.dep, upstream_commit='0' * 40)
        with self.assertRaisesRegex(inventory.InventoryError, 'inconsistent'):
            inventory.sources(self.super, dep)
        d = dict(path='sub', commit=self.commit, availability='inventoried', entries=[])
        with self.assertRaisesRegex(inventory.InventoryError, 'duplicate dependency'):
            inventory.sources(self.super, dict(self.dep, dependencies=[d, d]))

    def test_prose_and_template_unparsed_but_retained(self):
        (self.repo / 'README.cmake').write_text('This is prose, not an executable script.\n')
        (self.repo / 'template.cmake.in').write_text('@PACKAGE_INIT@\nset(VAR value)\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'invalid selected inputs')
        self.commit = self.git('rev-parse', 'HEAD').strip()
        self.super['upstream_commit'] = self.commit
        self.dep['upstream_commit'] = self.commit
        for p in ('README.cmake', 'template.cmake.in'):
            self.rows.append(dict(path=p, object_type='blob', mode='100644',
                                  object_id=self.git('rev-parse', f'{self.commit}:{p}').strip()))
        sp, dp = self.path / 'super.json', self.path / 'dep.json'
        sp.write_text(json.dumps(self.super))
        dp.write_text(json.dumps(self.dep))
        result = inventory.inventory(self.path, self.repo, sp, dp)
        self.assertFalse(result['valid'])
        self.assertTrue(result['coverage']['selected_exact_inputs_verified'])
        self.assertFalse(result['coverage']['all_commands_parsed'])
        self.assertFalse(result['coverage']['handwritten_semantic_review_complete'])
        self.assertEqual(result['counts']['files'], 4)
        self.assertEqual(result['counts']['parse_invalid_files'], 2)
        by_path = {r['path']: r for r in result['files']}
        self.assertEqual(by_path['README.cmake']['parse_status'], 'invalid-or-non-script')
        self.assertEqual(by_path['template.cmake.in']['parse_status'], 'template-unresolved')
        self.assertEqual(len(by_path['README.cmake']['sha256']), 64)
        self.assertEqual(by_path['template.cmake.in']['declarations'], [])

    def test_dependency_pin_must_match_parent_gitlink(self):
        d = dict(path='sub', commit=self.commit, availability='inventoried', entries=[])
        with self.assertRaisesRegex(inventory.InventoryError, 'gitlink provenance mismatch'):
            inventory.sources(self.super, dict(self.dep, dependencies=[d]))

    def test_fresh_complete_outputs_and_existing_preserved(self):
        sp, dp = self.path / 'super.json', self.path / 'dep.json'
        sp.write_text(json.dumps(self.super))
        dp.write_text(json.dumps(self.dep))
        result = inventory.inventory(self.path, self.repo, sp, dp)
        output, readable = self.path / 'output.json', self.path / 'summary.txt'
        inventory.write_outputs(result, output, readable)
        self.assertEqual(json.loads(output.read_text()), result)
        self.assertEqual(readable.read_text(), inventory.summary(result))
        before = output.read_bytes()
        with self.assertRaisesRegex(inventory.InventoryError, 'existing output'):
            inventory.write_outputs(result, output, readable)
        self.assertEqual(output.read_bytes(), before)

    def valid_result(self):
        sp, dp = self.path / 'super.json', self.path / 'dep.json'
        sp.write_text(json.dumps(self.super))
        dp.write_text(json.dumps(self.dep))
        return inventory.inventory(self.path, self.repo, sp, dp)

    def test_non_utf8_selected_blob_retained_and_unresolved(self):
        data = b'set(NAME \xff)\n'
        (self.repo / 'encoded.cmake').write_bytes(data)
        self.git('add', '.')
        self.git('commit', '-qm', 'non UTF-8 selected input')
        commit = self.git('rev-parse', 'HEAD').strip()
        self.super['upstream_commit'] = self.dep['upstream_commit'] = commit
        self.rows.append(dict(path='encoded.cmake', object_type='blob', mode='100644',
                              object_id=self.git('rev-parse', f'{commit}:encoded.cmake').strip()))
        result = self.valid_result()
        row = next(row for row in result['files'] if row['path'] == 'encoded.cmake')
        self.assertEqual(row['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(row['size'], len(data))
        self.assertEqual(row['parse_status'], 'encoding-unresolved')
        self.assertEqual(row['declarations'], [])
        self.assertFalse(result['valid'])
        self.assertTrue(result['coverage']['selected_exact_inputs_verified'])
        self.assertEqual(result['counts']['parse_invalid_files'], 1)

    def test_staging_failure_leaves_no_published_or_temporary_files(self):
        result = self.valid_result()
        output, readable = self.path / 'output.json', self.path / 'summary.txt'
        before = set(self.path.iterdir())
        with mock.patch.object(inventory.os, 'fsync', side_effect=[None, OSError('fixture disk failure')]):
            with self.assertRaisesRegex(OSError, 'fixture disk failure'):
                inventory.write_outputs(result, output, readable)
        self.assertEqual(set(self.path.iterdir()), before)

    def test_second_publication_failure_rolls_back_first_preserves_competing_file(self):
        result = self.valid_result()
        output, readable = self.path / 'output.json', self.path / 'summary.txt'
        before = set(self.path.iterdir())
        real_link = inventory.os.link
        def competing_publish(source, target):
            if target == readable:
                readable.write_text('competing output')
            return real_link(source, target)
        with mock.patch.object(inventory.os, 'link', side_effect=competing_publish):
            with self.assertRaises(FileExistsError):
                inventory.write_outputs(result, output, readable)
        self.assertFalse(output.exists())
        self.assertEqual(readable.read_text(), 'competing output')
        self.assertEqual(set(self.path.iterdir()), before | {readable})

    def test_output_path_alias_rejected_without_writes(self):
        result = self.valid_result()
        output = self.path / 'output.json'
        with self.assertRaisesRegex(inventory.InventoryError, 'distinct fresh paths'):
            inventory.write_outputs(result, output, output)
        self.assertFalse(output.exists())

    def test_duplicate_json_key_rejected(self):
        path = self.path / 'bad.json'
        path.write_text('{"a":1,"a":2}')
        with self.assertRaisesRegex(inventory.InventoryError, 'duplicate JSON key'):
            inventory.load(path)


if __name__ == '__main__':
    unittest.main()
