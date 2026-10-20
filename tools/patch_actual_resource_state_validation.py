#!/usr/bin/env python3
"""Test-only actual ResourceState construction/control-block/death validation.

Apply after resource-state death and the ordinary actual-cleanup instrumentation.
No RPC/guest path or logical resource-policy ownership is activated.
"""
from pathlib import Path


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('actual ResourceState test anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


def patch_actual_resource_state_validation(files):
    files = dict(files)
    state = files['resource-server-state.inc']
    state = '''void dearust_actual_state_after_constructor() noexcept;
void dearust_actual_state_disposed() noexcept;
''' + state
    state = once(state, '        if (!gate) throw std::bad_alloc();',
                 '        if (!gate) throw std::bad_alloc();\n        ::dearust_actual_state_after_constructor();')
    state = once(state, '    ~ResourceState() noexcept {',
                 '    ~ResourceState() noexcept {\n        ::dearust_actual_state_disposed();')
    files['resource-server-state.inc'] = state
    files['process.hpp'] = once(files['process.hpp'], '    void resourceDying() noexcept;',
        '    void resourceDying() noexcept;\n    static void runActualResourceStateValidation();')
    files['process.cpp'] = '#include <chrono>\n#include <thread>\n#include <cstring>\n#include <cstdio>\n' + files['process.cpp']
    files['process.cpp'] += '\n' + Path(__file__).with_name('resource-state-actual-validation.inc').read_text()
    files['server.hpp'] = once(files['server.hpp'], '\t\tvoid monitorProcess(std::shared_ptr<Process> process);',
        '\t\tvoid monitorProcess(std::shared_ptr<Process> process);\n'
        '        void discardActualStateProcessMonitor(int descriptor);')
    server = files['server.cpp']
    server = once(server, '\tThread::initializeResourceCleanup();',
        '\tThread::initializeResourceCleanup();\n'
        '\tProcess::runActualResourceStateValidation();\n'
        '\tdearust_actual_state_finish_validation();\n\t_monitorsWaitingToDie.clear();')
    server = ('bool dearust_actual_state_allocation_denied() noexcept;\n'
              'void dearust_actual_state_finish_validation() noexcept;\n' + server)
    server = once(server, 'bool dearust_actual_cleanup_allocation_denied() noexcept {',
        'bool dearust_actual_cleanup_allocation_denied() noexcept {\n'
        '    if (dearust_actual_state_allocation_denied()) return true;')
    server += '''
void dearust_actual_state_set_lane_denial(bool value) noexcept {
    actualCleanupCheckAllocations = value;
}
unsigned dearust_actual_state_lane_attempts() noexcept {
    return actualCleanupAllocationAttempts.load();
}
void dearust_actual_state_finish_validation() noexcept {
    // The separate ordinary lifecycle suite starts its disposal observations
    // after the fully drained ResourceState test and its owning Process.
    actualCleanupTaskDisposals = 0;
    actualCleanupThreadDisposals = 0;
}
void DarlingServer::Server::discardActualStateProcessMonitor(int descriptor) {
    std::shared_ptr<Monitor> selected;
    {
        std::lock_guard<std::mutex> guard(_monitorsLock);
        for (const auto& monitor: _monitors) {
            if (monitor->fd()->fd() != descriptor) continue;
            if (selected) std::abort();
            selected = monitor;
        }
    }
    if (!selected) std::abort();
    removeMonitor(selected);
}
'''
    files['server.cpp'] = server
    return files
