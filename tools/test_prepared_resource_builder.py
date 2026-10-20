"""Ordinary owned-builder preservation checks; compilation/linking are mocked."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import build_prepared_resource_server as builder


class PreparedBuilderPreservation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.candidate = self.root / 'candidate'
        self.output = self.root / 'owned'
        self.build = self.root / 'reference-build'
        self.reference = self.root / 'reference-source'
        self.source = self.reference / 'src/external/darlingserver'
        self.build.mkdir()
        self.source.mkdir(parents=True)
        (self.build / 'CMakeCache.txt').write_text(
            'CMAKE_HOME_DIRECTORY:INTERNAL=' + str(self.reference) + '\n')
        self.write(self.source / 'duct-tape/src/task.c', '\t\t\ttask->context = context;\n')
        flags = self.build / 'src/external/darlingserver/duct-tape/CMakeFiles/darlingserver_duct_tape.dir/flags.make'
        self.write(flags, 'C_DEFINES = \nC_INCLUDES = \nC_FLAGS = \n')
        for name in ('process', 'thread', 'server', 'registry'):
            self.write(self.candidate / 'internal-include/darlingserver' / (name + '.hpp'), 'header\n')
        for name in ('process', 'thread', 'server', 'call'):
            self.write(self.candidate / 'src' / (name + '.cpp'), 'source\n')
        for name in ('resource-policy-host.o', 'libresource-policy.a', 'src/server.cpp.o', 'linked/darlingserver'):
            self.write(self.candidate / name, 'frozen candidate\n')
        self.original = self.root / 'original.o'
        self.archive = self.root / 'original.a'
        self.write(self.original, 'original link object\n')
        self.write(self.archive, 'original archive\n')
        linked = self.candidate / 'linked'
        previous = dict(inputs=[dict(selected=str(self.original),
            selected_sha256=builder.digest(self.original), replaced=False, source='src/example.c')],
            server_inputs=[dict(selected=str(self.candidate / 'src/server.cpp.o'),
                selected_sha256=builder.digest(self.candidate / 'src/server.cpp.o'))],
            extra_archives=[dict(path=str(self.archive), sha256=builder.digest(self.archive))])
        (linked / 'relink.json').write_text(json.dumps(previous))
        source = self.candidate / 'src/server.cpp'
        self.base = dict(build=str(self.build), source=str(self.reference),
            server=str(linked / 'darlingserver'),
            sha256={str(source): builder.digest(source)},
            server_objects={'src/server.cpp': str(source) + '.o'},
            commands=[['compiler', '-c', str(source), '-o', str(source) + '.o']])
        self.save_base()

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def save_base(self):
        (self.candidate / 'build.json').write_text(json.dumps(self.base))

    def run_builder(self, mutation=None):
        def compile_owned(command, **unused):
            target = Path(command[command.index('-o') + 1])
            self.assertTrue(target.is_relative_to(self.output))
            self.write(target, 'owned object\n')
            self.write(Path(command[command.index('-MF') + 1]), 'owned dependency\n')
            if mutation:
                mutation()

        def relink_owned(build, output, *unused):
            self.assertEqual(build, self.build)
            self.assertTrue(output.is_relative_to(self.output))
            self.write(output / 'darlingserver', 'owned binary\n')

        with contextlib.ExitStack() as stack:
            for transform in ('patch_cleanup', 'patch_publication', 'patch_registration', 'patch_thread_death_barrier', 'patch_checkin'):
                stack.enter_context(mock.patch.object(builder, transform, side_effect=lambda files: files))
            stack.enter_context(mock.patch.object(builder, 'helper_files', return_value=[]))
            stack.enter_context(mock.patch.object(builder.subprocess, 'run', side_effect=compile_owned))
            stack.enter_context(mock.patch.object(builder, 'relink', side_effect=relink_owned))
            stack.enter_context(mock.patch('sys.argv', ['build', '--candidate', str(self.candidate), '--output', str(self.output)]))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            builder.main()

    def test_selected_bridge_source_is_preserved(self):
        bridge = self.root / 'bridge/task.c'
        self.write(bridge, 'lockset task lifecycle callbacks\n')
        record = dict(inputs=[dict(source='src/task.c', replaced=True,
                                  selected=str(bridge) + '.o')])
        self.assertEqual(builder.selected_duct_source(record, self.source, 'src/task.c'), bridge)

    def test_missing_selected_bridge_does_not_fall_back(self):
        record = dict(inputs=[dict(source='src/task.c', replaced=True,
                                  selected=str(self.root / 'missing.c.o'))])
        with self.assertRaisesRegex(ValueError, 'selected bridge source unavailable'):
            builder.selected_duct_source(record, self.source, 'src/task.c')

    def test_original_source_used_when_no_bridge_selected(self):
        self.assertEqual(builder.selected_duct_source(dict(inputs=[]), self.source, 'src/task.c'),
                         self.source / 'duct-tape/src/task.c')

    def test_ambiguous_selected_bridge_is_rejected(self):
        record = dict(inputs=[dict(source='src/task.c'), dict(source='src/task.c')])
        with self.assertRaisesRegex(ValueError, 'ambiguous selected duct source'):
            builder.selected_duct_source(record, self.source, 'src/task.c')

    def test_extra_generated_header_keeps_its_include_location_and_hash(self):
        header = self.root / 'selected/rpc.internal.h'
        self.write(header, 'owned generated declarations\n')
        with mock.patch.object(builder, 'EXTRA_SERVER_INPUTS', {'rpc.internal.h': header}):
            self.run_builder()
        owned = self.output / 'internal-include/darlingserver/rpc.internal.h'
        self.assertEqual(owned.read_bytes(), header.read_bytes())
        record = json.loads((self.output / 'build.json').read_text())
        self.assertEqual(record['initial_input_sha256'][str(header)], builder.digest(header))
        self.assertEqual(record['sha256'][str(owned)], builder.digest(owned))

    def test_missing_required_extra_build_record_prevents_success_manifest(self):
        with mock.patch.object(builder, 'EXTRA_BUILD_RECORDS', ['missing-commands.json']):
            with self.assertRaises(FileNotFoundError):
                self.run_builder()
        self.assertFalse((self.output / 'build.json').exists())

    def test_existing_output_and_candidate_are_preserved(self):
        self.write(self.output / 'keep', 'previous owned candidate\n')
        before = builder.digest(self.candidate / 'build.json')
        with self.assertRaises(SystemExit):
            self.run_builder()
        self.assertEqual((self.output / 'keep').read_text(), 'previous owned candidate\n')
        self.assertEqual(builder.digest(self.candidate / 'build.json'), before)

    def test_output_inside_frozen_candidate_is_rejected(self):
        self.output = self.candidate / 'new'
        with self.assertRaises(SystemExit):
            self.run_builder()
        self.assertFalse(self.output.exists())

    def test_changed_frozen_source_is_rejected_before_output_creation(self):
        self.write(self.candidate / 'src/server.cpp', 'changed source\n')
        with self.assertRaisesRegex(ValueError, 'frozen input changed'):
            self.run_builder()
        self.assertFalse(self.output.exists())

    def test_changed_link_object_is_rejected_before_output_creation(self):
        self.write(self.original, 'changed object\n')
        with self.assertRaisesRegex(ValueError, 'frozen link input changed'):
            self.run_builder()
        self.assertFalse(self.output.exists())

    def test_midbuild_link_object_mutation_prevents_success_manifest(self):
        with self.assertRaisesRegex(ValueError, 'build input changed during compilation'):
            self.run_builder(lambda: self.write(self.original, 'changed during compile\n'))
        self.assertFalse((self.output / 'build.json').exists())

    def test_midbuild_archive_mutation_prevents_success_manifest(self):
        with self.assertRaisesRegex(ValueError, 'build input changed during compilation'):
            self.run_builder(lambda: self.write(self.archive, 'changed during compile\n'))
        self.assertFalse((self.output / 'build.json').exists())

    def test_success_retains_all_link_inputs_in_mutation_detection(self):
        before = {str(path): builder.digest(path) for path in self.candidate.rglob('*') if path.is_file()}
        self.run_builder()
        report = json.loads((self.output / 'build.json').read_text())
        self.assertTrue(report['inputs_unchanged'])
        self.assertFalse(report['resource_ownership_activated'])
        self.assertEqual(report['initial_input_sha256'][str(self.original)], builder.digest(self.original))
        self.assertEqual(report['initial_input_sha256'][str(self.archive)], builder.digest(self.archive))
        self.assertTrue(all(builder.digest(Path(name)) == expected for name, expected in before.items()))


if __name__ == '__main__':
    unittest.main()
