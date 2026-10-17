#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise Cocoa GUI apps with matching original/Rust kernels on an isolated X display.

Requires apps built by build_gui_apps.py and an already running, task-owned X
server supplied explicitly with --display. Never starts/stops X servers or uses
:0. Calculator actions assert 42; TextEditor receives real XTest mouse/keyboard
input and an own-window screenshot for manual visual verification, without an
OCR/content assertion. Prefixes, execution logs and mapping evidence are retained.
"""
import argparse
import ctypes as C
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time

from check_mac_apps import digest, mapped_kernel, reap_launcher, run_process, shell_capture

P, U, I = C.c_void_p, C.c_ulong, C.c_int


class Attributes(C.Structure):
    _fields_ = [('x', I), ('y', I), ('width', I), ('height', I),
                ('border_width', I), ('depth', I), ('visual', P), ('root', U),
                ('class_', I), ('bit_gravity', I), ('win_gravity', I),
                ('backing_store', I), ('backing_planes', U), ('backing_pixel', U),
                ('save_under', I), ('colormap', U), ('map_installed', I),
                ('map_state', I), ('all_event_masks', C.c_long),
                ('your_event_mask', C.c_long), ('do_not_propagate_mask', C.c_long),
                ('override_redirect', I), ('screen', P)]


class Display:
    def __init__(self, name):
        self.x = C.CDLL('libX11.so.6')
        self.xt = C.CDLL('libXtst.so.6')
        signatures = {
            'XOpenDisplay': ([C.c_char_p], P),
            'XDefaultRootWindow': ([P], U),
            'XQueryTree': ([P, U, C.POINTER(U), C.POINTER(U),
                            C.POINTER(C.POINTER(U)), C.POINTER(C.c_uint)], I),
            'XFetchName': ([P, U, C.POINTER(P)], I),
            'XFree': ([P], I), 'XCloseDisplay': ([P], I),
            'XGetWindowAttributes': ([P, U, C.POINTER(Attributes)], I),
            'XSetInputFocus': ([P, U, I, U], I),
            'XGetInputFocus': ([P, C.POINTER(U), C.POINTER(I)], I),
            'XRaiseWindow': ([P, U], I),
            'XTranslateCoordinates': ([P, U, U, I, I, C.POINTER(I),
                                       C.POINTER(I), C.POINTER(U)], I),
            'XStringToKeysym': ([C.c_char_p], U),
            'XKeysymToKeycode': ([P, U], C.c_ubyte),
            'XSync': ([P, I], I), 'XFlush': ([P], I),
        }
        for method, (arguments, result) in signatures.items():
            function = getattr(self.x, method)
            function.argtypes, function.restype = arguments, result
        self.xt.XTestFakeKeyEvent.argtypes = [P, C.c_uint, I, U]
        self.xt.XTestFakeButtonEvent.argtypes = [P, C.c_uint, I, U]
        self.xt.XTestFakeMotionEvent.argtypes = [P, I, I, I, U]
        self.connection = self.x.XOpenDisplay(name.encode())
        if not self.connection:
            raise RuntimeError('cannot open explicitly supplied isolated display ' + name)
        # Apps can close windows during tree traversal. Ignore asynchronous X
        # errors, and use each query's result before inspecting returned storage.
        self.error_handler = C.CFUNCTYPE(I, P, P)(lambda display, event: 0)
        self.x.XSetErrorHandler.argtypes = [type(self.error_handler)]
        self.x.XSetErrorHandler(self.error_handler)
        self.root = self.x.XDefaultRootWindow(self.connection)

    def windows(self):
        found, seen = [], set()

        def visit(window, depth):
            if window in seen or len(seen) >= 10000:
                return
            seen.add(window)
            attributes, name = Attributes(), P()
            if self.x.XGetWindowAttributes(self.connection, window, C.byref(attributes)):
                self.x.XFetchName(self.connection, window, C.byref(name))
                title = C.string_at(name).decode(errors='replace') if name else ''
                if name:
                    self.x.XFree(name)
                found.append({'window_id': window, 'title': title,
                              'map_state': attributes.map_state,
                              'width': attributes.width, 'height': attributes.height})
            if depth:
                root, parent, children, count = U(), U(), C.POINTER(U)(), C.c_uint()
                if self.x.XQueryTree(self.connection, window, C.byref(root), C.byref(parent),
                                     C.byref(children), C.byref(count)):
                    for index in range(min(count.value, 10000)):
                        visit(children[index], depth - 1)
                    if children:
                        self.x.XFree(children)
        visit(self.root, 5)
        self.x.XSync(self.connection, 0)
        return found

    def type_editor(self, window):
        connection, number = self.connection, window['window_id']
        current = next((item for item in self.windows() if item['window_id'] == number), None)
        if not current or current['map_state'] != 2 or 'TextEditor' not in current['title']:
            raise RuntimeError('target is not the newly mapped TextEditor window')
        self.x.XRaiseWindow(connection, number)
        self.x.XSetInputFocus(connection, number, 2, 0)
        x, y, child = I(), I(), U()
        if not self.x.XTranslateCoordinates(connection, number, self.root, 70, 80,
                                            C.byref(x), C.byref(y), C.byref(child)):
            raise RuntimeError('cannot locate TextEditor content on isolated display')
        if not self.xt.XTestFakeMotionEvent(connection, -1, x.value, y.value, 0):
            raise RuntimeError('XTest mouse motion failed')
        for pressed in (1, 0):
            if not self.xt.XTestFakeButtonEvent(connection, 1, pressed, 0):
                raise RuntimeError('XTest content click failed')
        self.x.XSync(connection, 0)
        time.sleep(0.2)
        text = 'rust gui edit verified'
        for character in text:
            focused, revert = U(), I()
            self.x.XGetInputFocus(connection, C.byref(focused), C.byref(revert))
            if focused.value != number:
                raise RuntimeError('TextEditor lost input focus; refusing to send keys')
            symbol = self.x.XStringToKeysym(b'space' if character == ' ' else character.encode())
            key = self.x.XKeysymToKeycode(connection, symbol)
            if not key:
                raise RuntimeError('X keyboard has no key for ' + repr(character))
            for pressed in (1, 0):
                if not self.xt.XTestFakeKeyEvent(connection, key, pressed, 0):
                    raise RuntimeError('XTest key injection failed')
            self.x.XFlush(connection)
            time.sleep(0.015)
        self.x.XSync(connection, 0)
        return text

    def close(self):
        self.x.XCloseDisplay(self.connection)


def wait_window(display, app, excluded, timeout):
    deadline = time.monotonic() + timeout
    title = 'Darling Rust Calculator' if app == 'Calculator' else 'TextEditor'
    while time.monotonic() < deadline:
        for item in display.windows():
            if (item['window_id'] not in excluded and item['map_state'] == 2
                    and title in item['title'] and item['width'] > 100 and item['height'] > 100):
                return item
        time.sleep(0.05)
    raise RuntimeError('no newly mapped ' + app + ' window before deadline')


def terminate_launcher(process):
    # shutdown kills the namespace first; its setuid control CLI needs time to
    # observe EOF and exit before attempting a signal we may not be allowed to send.
    return reap_launcher(process, grace=5)


def variant(args, name, kernel, display):
    output = args.output / name
    output.mkdir()
    reservation = Path(tempfile.mkdtemp(prefix='dr-gui-', dir='/tmp'))
    prefix = reservation / 'p'
    cache = output / 'mesa-cache'
    cache.mkdir()
    env = {key: value for key, value in os.environ.items()
           if key in ('PATH', 'HOME', 'USER', 'LOGNAME', 'XAUTHORITY')}
    env.update(DPREFIX=str(prefix), DISPLAY=args.display, LC_ALL='C',
               MESA_SHADER_CACHE_DIR=str(cache))
    record = {'variant': name, 'prefix': str(prefix), 'display': args.display,
              'kernel_sha256': digest(kernel), 'results': []}
    launchers = []
    try:
        record['setup'] = run_process([args.launcher, '--version'], env, args.timeout)
        if record['setup']['exit_code'] != 0 or record['setup']['timed_out']:
            raise RuntimeError('prefix setup failed')
        temporary = prefix / 'private/tmp'
        temporary.mkdir(parents=True, exist_ok=True)
        temporary.chmod(0o1777)
        (prefix / 'home').mkdir(exist_ok=True)
        staged = prefix / 'usr/lib/system/libsystem_kernel.dylib'
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(kernel, staged)
        staged.chmod(0o755)
        record['staged_kernel_sha256'] = digest(staged)
        record['boot'] = run_process([args.launcher, 'shell', '/bin/echo', 'GUI_BOOT_OK'],
                                     env, args.timeout,
                                     on_timeout=lambda: run_process([args.launcher, 'shutdown'], env, 5))
        if (record['boot']['exit_code'] != 0 or record['boot']['timed_out']
                or 'GUI_BOOT_OK' not in record['boot']['stdout']):
            raise RuntimeError('isolated prefix first boot failed')
        record['kernel_mappings'] = mapped_kernel(staged)
        if not record['kernel_mappings']:
            raise RuntimeError('no process maps the task-owned kernel copy')
        for app in ('Calculator', 'TextEditor'):
            result = {'name': app}
            record['results'].append(result)
            excluded = {item['window_id'] for item in display.windows()}
            binary = args.apps / (app + '.app') / 'Contents/MacOS' / app
            result['executable_sha256'] = digest(binary)
            command, stdout, stderr, status = shell_capture(
                ['/Volumes/SystemRoot' + str(binary)], output, app)
            command = ('export DISPLAY=' + shlex.quote(args.display)
                       + ' GUI_PROBE_SECONDS=3 MESA_SHADER_CACHE_DIR='
                       + shlex.quote(str(cache)) + '; ' + command)
            with (output / (app + '.launcher.stdout')).open('wb') as launcher_out, \
                    (output / (app + '.launcher.stderr')).open('wb') as launcher_err:
                process = subprocess.Popen([args.launcher, 'shell', '/bin/bash', '-c', command],
                                           env=env, stdout=launcher_out, stderr=launcher_err,
                                           start_new_session=True)
                launchers.append(process)
                try:
                    window = wait_window(display, app, excluded, args.timeout)
                    result['mapped_window'] = window
                    result['kernel_mappings_while_window_mapped'] = mapped_kernel(staged)
                    if app == 'TextEditor':
                        result['typed_text'] = display.type_editor(window)
                        time.sleep(0.6)
                        result['content_assertion'] = 'Manual screenshot inspection required; no OCR assertion'
                    screenshot = output / (app + '.png')
                    subprocess.run(['import', '-display', args.display, '-window',
                                    str(window['window_id']), str(screenshot)],
                                   check=True, timeout=args.timeout, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    result['screenshot'] = str(screenshot)
                    result['screenshot_sha256'] = digest(screenshot)
                    if app == 'Calculator':
                        process.wait(timeout=args.timeout)
                        text = stdout.read_text(errors='replace') if stdout.exists() else ''
                        child_status = int(status.read_text().strip()) if status.exists() else None
                        result.update(launcher_exit_code=process.returncode, child_exit_code=child_status)
                        result['pass'] = (process.returncode == 0 and child_status == 0
                                          and 'CALCULATOR_SELFTEST PASS value=42 visible=1' in text)
                    else:
                        result['input_and_capture_pass'] = process.poll() is None
                        result['pass'] = result['input_and_capture_pass']
                except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                    result.update(error=str(error), **{'pass': False})
                result['stdout'] = stdout.read_text(errors='replace') if stdout.exists() else ''
                result['stderr'] = stderr.read_text(errors='replace') if stderr.exists() else ''
            (output / 'results.json').write_text(json.dumps(record, indent=2) + '\n')
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        record['error'] = str(error)
    finally:
        if prefix.exists():
            try:
                record['shutdown'] = run_process([args.launcher, 'shutdown'], env, args.timeout)
            except (OSError, subprocess.SubprocessError) as error:
                record['shutdown'] = {'error': str(error)}
        record['launcher_cleanup'] = [terminate_launcher(process) for process in launchers]
        if any(not item['reaped'] for item in record['launcher_cleanup']):
            record['cleanup_failure'] = 'one or more launchers remain unreaped'
        (output / 'results.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apps', type=Path, required=True)
    parser.add_argument('--original-kernel', type=Path, required=True)
    parser.add_argument('--rust-kernel', type=Path, required=True)
    parser.add_argument('--display', required=True, help='Explicit task-owned local X display, e.g. :98; never :0')
    parser.add_argument('--output', type=Path, required=True, help='New exclusively owned output directory')
    parser.add_argument('--timeout', type=float, default=25)
    parser.add_argument('--launcher', default='/usr/local/bin/darling')
    args = parser.parse_args()
    match = re.fullmatch(r':([0-9]+)(?:\.[0-9]+)?', args.display)
    if not match or int(match[1]) == 0:
        parser.error('--display must explicitly name an isolated local display other than :0')
    if args.timeout <= 0 or args.timeout > 120:
        parser.error('--timeout must be greater than zero and at most 120 seconds')
    args.apps = args.apps.resolve(strict=True)
    for path in [args.original_kernel, args.rust_kernel] + [
            args.apps / (app + '.app') / 'Contents/MacOS' / app for app in ('Calculator', 'TextEditor')]:
        if not path.is_file():
            parser.error('missing regular input file: ' + str(path))
    args.output = args.output.absolute()
    args.output.mkdir(parents=True, exist_ok=False)
    display = Display(args.display)
    try:
        records = [variant(args, name, kernel, display) for name, kernel in
                   [('original', args.original_kernel.resolve()), ('rust', args.rust_kernel.resolve())]]
    finally:
        display.close()
    (args.output / 'comparison.json').write_text(json.dumps({
        'scope': 'Mixed installed runtime; Calculator action assertion and real TextEditor input/screenshots. '
                 'TextEditor rendered content requires manual screenshot inspection.',
        'variants': records}, indent=2) + '\n')
    return 0 if all(len(record['results']) == 2 and record.get('kernel_mappings')
                    and all(result.get('pass') for result in record['results'])
                    and 'error' not in record and 'cleanup_failure' not in record for record in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
