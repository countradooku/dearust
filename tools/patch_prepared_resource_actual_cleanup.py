#!/usr/bin/env python3
"""Optional test-only transforms for actual native dtape lifecycle validation."""
from pathlib import Path


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('actual cleanup test anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


def patch_actual_cleanup_validation(files, native_main_source):
    files = dict(files)
    declarations = ('\nvoid dearust_actual_cleanup_acquired();\n'
                    'void dearust_actual_cleanup_disposed(bool thread) noexcept;\n')
    for name in ('process.cpp', 'thread.cpp'):
        text = declarations + files[name]
        handles = ('_dtapeTask', '_dtapeForkWaitSemaphore') if name == 'process.cpp' else (
            '_dtapeThread', '_s2cPerformSempahore', '_s2cReplySempahore',
            '_s2cInterruptEnterSemaphore', '_s2cInterruptExitSemaphore')
        # Only preparation is injected, never bootstrap kernel constructors.
        begin = text.index('void DarlingServer::' + ('Process' if name == 'process.cpp' else 'Thread') + '::prepareKernelHandles()')
        end = text.index('\nvoid DarlingServer::' + ('Process' if name == 'process.cpp' else 'Thread') + '::abortKernelHandles()', begin)
        prefix, body, suffix = text[:begin], text[begin:end], text[end:]
        for handle in handles:
            old = f'if (!{handle}) throw std::bad_alloc();'
            body = once(body, old, old + '\n        dearust_actual_cleanup_acquired();')
        text = prefix + body + suffix
        owner = 'Process' if name == 'process.cpp' else 'Thread'
        old = f'void DarlingServer::{owner}::_dispose() {{'
        text = once(text, old, old + '\n    dearust_actual_cleanup_disposed(' +
                    ('false' if owner == 'Process' else 'true') + ');')
        files[name] = text
    files['registry.hpp'] = declarations + once(files['registry.hpp'],
        '                entry->preparePublication();',
        '                entry->preparePublication();\n                dearust_actual_cleanup_acquired();')
    files['thread.hpp'] = once(files['thread.hpp'], '\t\tfriend class Process;',
                              '\t\tfriend class Process;\n        friend class Server;')
    files['server.hpp'] = once(files['server.hpp'], '\t\tvoid _worker(std::shared_ptr<Thread> thread);',
        '\t\tvoid _worker(std::shared_ptr<Thread> thread);\n        void _runActualPreparedCleanupValidation();')
    text = ('#include <atomic>\n#include <chrono>\n#include <dirent.h>\n#include <sys/syscall.h>\n#include <malloc.h>\n'
            'extern "C" unsigned long long dearust_prepared_task_saved_eid(void*);\n' + files['server.cpp'])
    text = once(text, '\tThread::initializeResourceCleanup();',
                '\tThread::initializeResourceCleanup();\n\t_runActualPreparedCleanupValidation();')
    text += '\n' + Path(__file__).with_name('prepared-resource-actual-cleanup.inc').read_text()
    files['server.cpp'] = text
    begin = native_main_source.index('int main(int argc, char** argv) {')
    interposition = Path(__file__).with_name('prepared-resource-actual-allocations.inc').read_text()
    files['darlingserver.cpp'] = native_main_source[:begin] + '\n' + interposition + '\n' + '''int main(int argc, char** argv) {
    if (argc != 2) return 2;
    signal(SIGPIPE, SIG_IGN);
    DarlingServer::Message::initializeServerCredentials();
    auto* server = new DarlingServer::Server(argv[1]);
    server->start();
    return 2;
}
'''
    return files
