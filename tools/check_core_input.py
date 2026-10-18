#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent owned-fixture CLI fault, alias and descriptor-pressure checks.

Run with --converter BINARY --output NEW_DIRECTORY. No installed runtime or
reference input is modified. A sparse /dev/shm fixture forces cross-filesystem
copying for the concurrent-truncation case and is removed before return.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import stat
import struct
import subprocess
import tempfile
import time


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def fixture(bits, mappings=(), dumped_length=4):
    wide = bits == 64
    width, header, phsize = (8, 64, 56) if wide else (4, 52, 32)
    integer = lambda value: value.to_bytes(width, 'little')
    files = integer(len(mappings)) + integer(4096)
    files += b''.join(integer(value) for start, payload, _ in mappings
                      for value in (start, start + len(payload), 0))
    files += b''.join(os.fsencode(path) + b'\0' for _, _, path in mappings)
    registers = bytearray(336 if wide else 144)
    struct.pack_into('<I', registers, 32 if wide else 24, 1729)
    register_start = 112 if wide else 72
    for index in range(27 if wide else 17):
        registers[register_start + index * width:register_start + (index + 1) * width] = integer(0x100 + index)

    def note(kind, payload):
        return (struct.pack('<III', 5, len(payload), kind) + b'CORE\0\0\0\0'
                + payload + b'\0' * (-len(payload) % 4))

    notes = note(1, registers) + note(0x46494c45, files)
    note_offset, payload_offset = 4096, 8192
    if note_offset + len(notes) > payload_offset:
        payload_offset = (note_offset + len(notes) + 4095) & ~4095
    data = bytearray(payload_offset + min(dumped_length, 4))
    data[:7] = b'\x7fELF' + bytes((2 if wide else 1, 1, 1))
    struct.pack_into('<HHI', data, 16, 4, 62 if wide else 3, 1)
    data[32 if wide else 28:(32 if wide else 28) + width] = integer(header)
    struct.pack_into('<HHH', data, 52 if wide else 40, header, phsize, 2)
    for index, kind, offset, address, length, size, flags in (
        (0, 4, note_offset, 0, len(notes), len(notes), 0),
        (1, 1, payload_offset, 0x1000, dumped_length, max(8, dumped_length), 6),
    ):
        position = header + index * phsize
        struct.pack_into('<I', data, position, kind)
        fields = ((4, flags, 4), (8, offset, 8), (16, address, 8),
                  (32, length, 8), (40, size, 8)) if wide else (
                      (4, offset, 4), (8, address, 4), (16, length, 4),
                      (20, size, 4), (24, flags, 4))
        for at, value, count in fields:
            data[position + at:position + at + count] = value.to_bytes(count, 'little')
    data[note_offset:note_offset + len(notes)] = notes
    data[payload_offset:] = b'CORE'[:dumped_length]
    return bytes(data), payload_offset


def verify_segments(path, bits, mappings):
    data = path.read_bytes()
    wide = bits == 64
    assert struct.unpack_from('<I', data)[0] == (0xfeedfacf if wide else 0xfeedface)
    position = 32 if wide else 28
    command_end = position + struct.unpack_from('<I', data, 20)[0]
    segments = {}
    threads = 0
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        kind, size = struct.unpack_from('<II', data, position)
        assert size >= 8 and position + size <= command_end
        if kind in (1, 0x19):
            address, memory, offset, length = struct.unpack_from('<QQQQ' if wide else '<IIII', data, position + 24)
            assert memory == length and offset >= command_end and offset + length <= len(data)
            assert address not in segments
            segments[address] = data[offset:offset + length]
        elif kind == 4:
            threads += 1
        position += size
    assert position == command_end and threads == 1
    expected = {0x1000: b'CORE' + b'\0' * 4}
    expected.update((start, payload) for start, payload, _ in mappings)
    assert segments == expected, (segments.keys(), expected.keys())


class Checks:
    def __init__(self, converter, output):
        self.converter, self.output = converter, output
        self.results = []
        self.env = dict(os.environ, HOME=str(output / 'home'), DPREFIX=str(output / 'overlay'))

    def launch(self, source, destination, nofile=None, fsize=None):
        def limits():
            if nofile is not None:
                resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))
            if fsize is not None:
                signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
                resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        return subprocess.Popen([str(self.converter), str(source), str(destination)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=self.env,
                                preexec_fn=limits if nofile is not None or fsize is not None else None)

    def finish(self, name, process, success, **details):
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            self.results.append(dict(name=name, passed=False, timeout=True,
                                     stderr=stderr.decode(errors='replace'), **details))
            raise AssertionError(name + ': timed out')
        passed = process.returncode == 0 if success else process.returncode > 0
        self.results.append(dict(name=name, passed=passed, returncode=process.returncode,
                                 stdout=stdout.decode(errors='replace'),
                                 stderr=stderr.decode(errors='replace'), **details))
        assert passed, self.results[-1]

    def ordinary(self, bits):
        directory = self.output / str(bits)
        directory.mkdir()
        source, destination = directory / 'pressure.elf', directory / 'pressure.macho'
        mappings = []
        for index in range(100):
            path = directory / f'image-{index:03d}'
            payload = bytes((index,)) * 64
            path.write_bytes(payload)
            mappings.append((0x100000 + index * 4096, payload, path))
        source.write_bytes(fixture(bits, mappings)[0])
        for limit in (16, 32):
            self.finish(f'{bits}-100-backings-nofile-{limit}', self.launch(source, destination, limit), True)
            verify_segments(destination, bits, mappings)
        missing = [(0x2000 + index * 4096, b'\0' * 64, directory / f'missing-{index}')
                   for index in range(3)]
        source, destination = directory / 'missing.elf', directory / 'missing.macho'
        source.write_bytes(fixture(bits, missing)[0])
        self.finish(f'{bits}-missing-nofile-5', self.launch(source, destination, 5), True)
        verify_segments(destination, bits, missing)

        missing_device = Path(f'/dev/dearust-core-input-missing-{os.getpid()}-{bits}')
        assert not os.path.lexists(missing_device)
        skipped = [(0x2000, b'\0' * 64, missing_device)]
        source, destination = directory / 'missing-device.elf', directory / 'missing-device.macho'
        source.write_bytes(fixture(bits, skipped)[0])
        self.finish(f'{bits}-missing-skipped-device-nofile-5', self.launch(source, destination, 5), True)
        verify_segments(destination, bits, skipped)
        skipped = [(0x2000, b'\0' * 64, Path('/dev/zero'))]
        source, destination = directory / 'existing-device.elf', directory / 'existing-device.macho'
        source.write_bytes(fixture(bits, skipped)[0])
        self.finish(f'{bits}-existing-skipped-device-nofile-5', self.launch(source, destination, 5), True)
        verify_segments(destination, bits, skipped)

        # Nonregular backing inputs must fail before touching the destination.
        source, destination = directory / 'directory-backing.elf', directory / 'preserve.macho'
        source.write_bytes(fixture(bits, [(0x2000, b'\0' * 32, directory)])[0])
        destination.write_bytes(b'preserve destination')
        self.finish(f'{bits}-directory-backing-rejected', self.launch(source, destination), False)
        assert destination.read_bytes() == b'preserve destination'

        # /proc/self/mem is regular, but this CLI has no mapping at address zero:
        # the executable-flags read must report the real EIO, not default flags.
        source = directory / 'flags-eio.elf'
        source.write_bytes(fixture(bits, [(0x2000, b'\0' * 32, Path('/proc/self/mem'))])[0])
        self.finish(f'{bits}-flags-read-eio', self.launch(source, destination), False)
        assert '(os error 5)' in self.results[-1]['stderr']
        assert destination.read_bytes() == b'preserve destination'

        source = directory / 'dev-full.elf'
        source.write_bytes(fixture(bits)[0])
        full = directory / 'full-output'
        full.symlink_to('/dev/full')
        self.finish(f'{bits}-dev-full', self.launch(source, full), False)

        # Exercise the final sparse extension and active payload write paths
        # separately. Ignoring SIGXFSZ makes the kernel return EFBIG to the CLI.
        for sparse in (True, False):
            name = 'sparse-extension' if sparse else 'payload-write'
            claimed = 2 * 1024**2 if sparse else 128 * 1024
            data, payload = fixture(bits, dumped_length=4 if sparse else claimed)
            data = bytearray(data)
            if sparse:
                header, phsize, memory_at, fmt = (64, 56, 40, '<Q') if bits == 64 else (52, 32, 20, '<I')
                struct.pack_into(fmt, data, header + phsize + memory_at, claimed)
            else:
                data[payload:] = bytes(index % 251 for index in range(claimed))
            source, limited_output = directory / f'{name}.elf', directory / f'{name}.macho'
            source.write_bytes(data)
            limited_output.write_bytes(b'preexisting nonzero bytes')
            self.finish(f'{bits}-{name}-fsize-65536',
                        self.launch(source, limited_output, fsize=65536), False,
                        file_size_limit=65536, claimed_segment_size=claimed)
            output_length = limited_output.stat().st_size
            self.results[-1]['output_bytes'] = output_length
            assert output_length <= 65536 and output_length < claimed

        # Both source kinds must survive destination hardlinks and symlinks.
        for backing in (False, True):
            source = directory / f'alias-backing-{backing}.elf'
            image = directory / 'alias-image'
            image.write_bytes(b'backing image bytes' * 4)
            mapped = [(0x2000, image.read_bytes(), image)] if backing else []
            source.write_bytes(fixture(bits, mapped)[0])
            target = image if backing else source
            expected = target.read_bytes()
            for symlink in (False, True):
                alias = directory / f'alias-{backing}-{symlink}'
                if symlink:
                    alias.symlink_to(target)
                else:
                    os.link(target, alias)
                self.finish(f'{bits}-alias-backing-{backing}-symlink-{symlink}',
                            self.launch(source, alias), False)
                assert target.read_bytes() == expected and alias.read_bytes() == expected

        # A FIFO must be rejected without waiting for a writer, both as a
        # reconstruction image and as the input itself.
        fifo = directory / 'flags-fifo'
        os.mkfifo(fifo, 0o600)
        source = directory / 'fifo-backing.elf'
        source.write_bytes(fixture(bits, [(0x2000, b'\0' * 32, fifo)])[0])
        destination.write_bytes(b'preserve destination')
        try:
            self.finish(f'{bits}-fifo-backing-no-writer', self.launch(source, destination), False)
            assert destination.read_bytes() == b'preserve destination'
            self.finish(f'{bits}-fifo-input-no-writer', self.launch(fifo, destination), False)
            assert destination.read_bytes() == b'preserve destination'
        finally:
            fifo.unlink()

        # Preserve sequential nonregular output support. A connected FIFO must
        # receive exactly the bytes emitted to a regular file; /dev/null works.
        source = directory / 'fifo-output.elf'
        dumped = 128 * 1024
        stream_data, stream_payload = fixture(bits, dumped_length=dumped)
        stream_data = bytearray(stream_data)
        stream_header, stream_phsize, stream_memory_at, stream_fmt = (64, 56, 40, '<Q') if bits == 64 else (52, 32, 20, '<I')
        struct.pack_into(stream_fmt, stream_data, stream_header + stream_phsize + stream_memory_at,
                         dumped + 2 * 1024**2)
        stream_data[stream_payload:] = bytes(index % 251 for index in range(dumped))
        source.write_bytes(stream_data)
        expected_output = directory / 'fifo-expected.macho'
        reference = self.launch(source, expected_output)
        reference.communicate(timeout=30)
        assert reference.returncode == 0
        output_fifo = directory / 'output-fifo'
        os.mkfifo(output_fifo, 0o600)
        reader = None
        fifo_process = None
        try:
            reader = os.open(output_fifo, os.O_RDONLY | os.O_NONBLOCK)
            fifo_process = self.launch(source, output_fifo)
            received = bytearray()
            deadline = time.monotonic() + 15
            complete = False
            while time.monotonic() < deadline:
                try:
                    block = os.read(reader, 65536)
                except BlockingIOError:
                    block = None
                if block:
                    received.extend(block)
                elif block == b'' and fifo_process.poll() is not None:
                    complete = True
                    break
                else:
                    time.sleep(0.0001)
            assert complete, 'connected FIFO did not finish streaming'
            self.finish(f'{bits}-connected-fifo-output', fifo_process, True,
                        dumped_bytes=dumped, zero_tail_bytes=2 * 1024**2)
            assert bytes(received) == expected_output.read_bytes()
            (directory / 'fifo-received.macho').write_bytes(received)
            self.finish(f'{bits}-dev-null-output', self.launch(source, Path('/dev/null')), True)
        finally:
            if fifo_process is not None and fifo_process.poll() is None:
                fifo_process.kill()
                fifo_process.communicate()
            if reader is not None:
                os.close(reader)
            output_fifo.unlink()

        # Identity-only selections still protect skipped device and empty
        # backing mappings against destructive destination aliases.
        source = directory / 'skipped-device-alias.elf'
        source.write_bytes(fixture(bits, [(0x2000, b'\0' * 32, Path('/dev/null'))])[0])
        self.finish(f'{bits}-skipped-device-output-alias', self.launch(source, Path('/dev/null')), False)
        assert 'aliases a backing file' in self.results[-1]['stderr']
        image = directory / 'zero-length-alias-image'
        original_image = b'nonzero empty-mapping backing image must remain intact'
        image.write_bytes(original_image)
        source = directory / 'zero-length-alias.elf'
        source.write_bytes(fixture(bits, [(0x2000, b'', image)])[0])
        self.finish(f'{bits}-zero-length-backing-alias', self.launch(source, image), False)
        assert image.read_bytes() == original_image

        # An unknown 64MiB note creates a real metadata-read window without a
        # blocking special file or interposed I/O. Its opaque bytes are sparse.
        source = directory / 'metadata-truncated.elf'
        original, _ = fixture(bits)
        data = bytearray(original)
        header, phsize, fmt = (64, 56, '<Q') if bits == 64 else (52, 32, '<I')
        size_at, memory_at, offset_at = (32, 40, 8) if bits == 64 else (16, 20, 4)
        original_note_size = struct.unpack_from(fmt, data, header + size_at)[0]
        unknown = struct.pack('<III', 5, 64 * 1024**2, 0x7ffffffe) + b'TEST\0\0\0\0'
        note_end = 4096 + original_note_size
        notes_size = original_note_size + len(unknown) + 64 * 1024**2
        payload = (4096 + notes_size + 4095) & ~4095
        struct.pack_into(fmt, data, header + size_at, notes_size)
        struct.pack_into(fmt, data, header + memory_at, notes_size)
        struct.pack_into(fmt, data, header + phsize + offset_at, payload)
        with source.open('wb') as stream:
            stream.write(data[:note_end])
            stream.write(unknown)
            stream.seek(payload)
            stream.write(b'CORE')
        destination.write_bytes(b'preserve destination')
        process = self.launch(source, destination)
        established = False
        observed_position = None
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    for descriptor in Path(f'/proc/{process.pid}/fd').iterdir():
                        try:
                            if os.readlink(descriptor) != str(source):
                                continue
                            info = Path(f'/proc/{process.pid}/fdinfo/{descriptor.name}').read_text()
                            position = int(next(line.split()[1] for line in info.splitlines()
                                                if line.startswith('pos:')))
                            if position >= 4096:
                                with source.open('r+b') as stream:
                                    stream.truncate(0)
                                observed_position = position
                                established = True
                                break
                        except FileNotFoundError:
                            continue
                except FileNotFoundError:
                    pass
                if established:
                    break
                assert process.poll() is None, 'converter finished before metadata truncation window'
                time.sleep(0.0001)
            assert established, 'could not establish metadata truncation window'
            self.finish(f'{bits}-metadata-truncate-before-output', process, False,
                        observed_input_position_before_truncate=observed_position,
                        original_input_bytes=payload + 4, unknown_note_bytes=64 * 1024**2)
            assert destination.read_bytes() == b'preserve destination'
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def concurrent_truncate(self, bits):
        if not Path('/dev/shm').is_dir() or os.stat('/dev/shm').st_dev == self.output.stat().st_dev:
            raise AssertionError('cross-filesystem sparse truncation test requires distinct /dev/shm')
        destination = self.output / f'concurrent-{bits}.macho'
        with tempfile.TemporaryDirectory(prefix='dearust-core-input-', dir='/dev/shm') as temporary:
            source = Path(temporary) / 'owned-sparse.elf'
            data, payload = fixture(bits, dumped_length=1024**3)
            with source.open('wb') as stream:
                stream.write(data)
                stream.truncate(payload + 1024**3)
            process = self.launch(source, destination)
            established = False
            observed = 0
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    observed = destination.stat().st_size if destination.exists() else 0
                    if observed > 4096:
                        with source.open('r+b') as stream:
                            stream.truncate(0)
                        established = True
                        break
                    assert process.poll() is None, 'converter finished before truncation window'
                    time.sleep(0.0001)
                assert established, 'could not establish concurrent truncation window'
                self.finish(f'{bits}-concurrent-payload-truncate', process, False,
                            observed_output_before_truncate=observed,
                            source_filesystem_device=source.stat().st_dev,
                            output_filesystem_device=self.output.stat().st_dev)
                assert 4096 < destination.stat().st_size < 1024**3
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def crossfs_transfer_faults(self, bits):
        assert Path('/dev/shm').is_dir() and os.stat('/dev/shm').st_dev != self.output.stat().st_dev
        dumped = 128 * 1024
        data, payload = fixture(bits, dumped_length=dumped)
        data = bytearray(data)
        header, phsize, memory_at, fmt = (64, 56, 40, '<Q') if bits == 64 else (52, 32, 20, '<I')
        struct.pack_into(fmt, data, header + phsize + memory_at, dumped + 2 * 1024**2)
        data[payload:] = bytes(index % 251 for index in range(dumped))
        with tempfile.TemporaryDirectory(prefix='dearust-core-transfer-', dir='/dev/shm') as temporary:
            source = Path(temporary) / 'owned.elf'
            source.write_bytes(data)
            original_hash = sha(source)
            expected = self.output / f'crossfs-{bits}-reference.macho'
            reference = self.launch(source, expected)
            reference.communicate(timeout=30)
            assert reference.returncode == 0
            destination = self.output / f'crossfs-{bits}-nofile5.macho'
            destination.write_bytes(b'old destination bytes')
            self.finish(f'{bits}-crossfs-optional-pipe-nofile-5',
                        self.launch(source, destination, nofile=5), True,
                        source_sha256_before=original_hash)
            assert destination.read_bytes() == expected.read_bytes()
            assert sha(source) == original_hash
            self.results[-1]['source_sha256_after'] = sha(source)
            destination = self.output / f'crossfs-{bits}-fsize65536.macho'
            self.finish(f'{bits}-crossfs-splice-write-fsize-65536',
                        self.launch(source, destination, fsize=65536), False,
                        source_sha256_before=original_hash, file_size_limit=65536)
            assert '(os error 27)' in self.results[-1]['stderr']
            assert destination.stat().st_size <= 65536 < dumped
            assert sha(source) == original_hash
            self.results[-1].update(source_sha256_after=sha(source),
                                    output_bytes=destination.stat().st_size)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--converter', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    converter, output = args.converter.resolve(strict=True), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    checks = Checks(converter, output)
    failure = None
    try:
        for bits in (32, 64):
            checks.ordinary(bits)
            checks.concurrent_truncate(bits)
            checks.crossfs_transfer_faults(bits)
    except Exception as error:
        failure = f'{type(error).__name__}: {error}'
    files = {}
    for path in output.rglob('*'):
        if not path.is_symlink() and stat.S_ISREG(path.stat().st_mode):
            files[str(path.relative_to(output))] = dict(bytes=path.stat().st_size, sha256=sha(path))
    report = dict(converter=str(converter), converter_sha256=sha(converter),
                  passed=failure is None, failure=failure, cases=checks.results, files=files)
    (output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(passed=report['passed'], cases=len(checks.results), failure=failure)))
    return 1 if failure else 0


if __name__ == '__main__':
    raise SystemExit(main())
