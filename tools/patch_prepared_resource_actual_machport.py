#!/usr/bin/env python3
"""Test-only instrumentation for real XNU Mach-port callback/detach lifetime."""
from pathlib import Path


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('actual Mach-port validation anchor is not unique: ' + old[:100])
    return text.replace(old, new, 1)


def instrument_actual_machport_c(files):
    files = dict(files)
    source = files['src/kqchan.c']
    declarations = ('void dearust_actual_mach_snapshot(void*);\n'
                    'void dearust_actual_mach_detach_begin(void*);\n'
                    'void dearust_actual_mach_waiter_waiting(void*);\n')
    source = declarations + source
    source = once(source, '\tcb(ctx);', '\tdearust_actual_mach_snapshot(kqchan);\n\tcb(ctx);')
    source = once(source, '\t\t\t\t\twcb(wctx);', '\t\t\t\t\tdearust_actual_mach_snapshot(kqchan);\n\t\t\t\t\twcb(wctx);')
    source = once(source, '\t\t\twait_result = thread_block(NULL);', '\t\t\tdearust_actual_mach_waiter_waiting(kqchan);\n\t\t\twait_result = thread_block(NULL);')
    source = once(source, '    filt_machportdetach(&kqchan->knote);',
        '    dearust_actual_mach_detach_begin(kqchan);\n    filt_machportdetach(&kqchan->knote);')
    files['src/kqchan.c'] = source + '\n' + Path(__file__).with_name('prepared-resource-actual-machport.c.inc').read_text()
    return files


def patch_actual_machport_validation(files, native_main_source):
    files = dict(files)
    files['kqchan.hpp'] = once(files['kqchan.hpp'], '\tclass Kqchan::MachPort: public Kqchan, public std::enable_shared_from_this<MachPort> {',
        '\tclass Kqchan::MachPort: public Kqchan, public std::enable_shared_from_this<MachPort> {\n'
        '\t\tfriend class Server;')
    declarations = ('extern "C" void dearust_actual_mach_promoted(void*);\n'
                    'extern "C" void dearust_actual_mach_disposed(void*);\n'
                    'extern "C" void dearust_actual_mach_notified(void*);\n')
    text = declarations + files['kqchan.cpp']
    text = once(text, 'if (auto channel = retirement->owner.lock()) channel->_notify();',
        'if (auto channel = retirement->owner.lock()) {\n'
        '            dearust_actual_mach_promoted(channel.get());\n            channel->_notify();\n'
        '            dearust_actual_mach_notified(channel.get());\n        }')
    text = once(text, 'if (retirement->handle) dtape_kqchan_mach_port_destroy(retirement->handle);',
        'if (retirement->handle) {\n'
        '            dtape_kqchan_mach_port_destroy(retirement->handle);\n'
        '            dearust_actual_mach_disposed(retirement->handle);\n        }')
    files['kqchan.cpp'] = text
    files['server.hpp'] = once(files['server.hpp'], '\t\tvoid _worker(std::shared_ptr<Thread> thread);',
        '\t\tvoid _worker(std::shared_ptr<Thread> thread);\n        void _runActualMachportValidation();')
    text = '#include <chrono>\n#include <thread>\n#include <atomic>\n#include <cstring>\n' + files['server.cpp']
    text = once(text, '\tThread::initializeResourceCleanup();',
        '\tThread::initializeResourceCleanup();\n\t_runActualMachportValidation();')
    files['server.cpp'] = text + '\n' + Path(__file__).with_name('prepared-resource-actual-machport.inc').read_text()
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
