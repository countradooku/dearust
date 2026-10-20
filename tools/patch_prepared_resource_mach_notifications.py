#!/usr/bin/env python3
"""Fixed Mach channel notification/reply transport after lifecycle transforms.

Read/modify kernel work still uses its ordinary fallible scheduling boundary.
Live notifications, output backpressure and completion do not allocate or enqueue
another kernel thread. Event rechecks run in the existing read/modify context.
"""
from pathlib import Path


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Mach notification anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


FIELDS = '''
        union PreparedMachReply {
            dserver_kqchan_reply_mach_port_modify_t modify;
            dserver_kqchan_reply_mach_port_read_t read;
            PreparedMachReply() : read{} {}
        } _preparedMachReply;
        size_t _preparedMachReplyLength = 0;
        bool _preparedMachNotificationPending = false;
        bool _preparedMachRequestInFlight = false;
        std::atomic<int> _preparedMachTransportError{0};
        bool _flushPreparedMachOutputLocked() noexcept;
        void _completePreparedMachRead(const dserver_kqchan_reply_mach_port_read_t&) noexcept;
        void _completePreparedMachModify() noexcept;
        bool _processPreparedTransportEvent(Monitor::Event) override;
'''


def patch_mach_notifications(files):
    files = dict(files)
    files['kqchan.hpp'] = once(files['kqchan.hpp'], '\t\tstd::unique_ptr<PreparedRetirement> _preparedRetirement;',
                              '\t\tstd::unique_ptr<PreparedRetirement> _preparedRetirement;\n' + FIELDS)
    files['server.hpp'] = once(files['server.hpp'], '\t\tvoid _worker(std::shared_ptr<Thread> thread);',
        '\t\tvoid _worker(std::shared_ptr<Thread> thread);\n'
        '    public:\n        bool rearmPreparedMonitor(const std::shared_ptr<Monitor>&) noexcept;\n    private:')
    files['server.cpp'] += '''
bool DarlingServer::Server::rearmPreparedMonitor(const std::shared_ptr<Monitor>& monitor) noexcept {
    if (!monitor) return true;
    std::unique_lock lock(monitor->_lock);
    if (!monitor->_server) return true;
    struct epoll_event settings = {};
    settings.data.ptr = monitor.get();
    settings.events = monitor->_events;
    return epoll_ctl(monitor->_server->_epollFD, EPOLL_CTL_MOD, monitor->_fd->fd(), &settings) == 0;
}
'''
    source = files['kqchan.cpp']
    begin = source.index('void DarlingServer::Kqchan::MachPort::_processMessages() {')
    end = source.index('//\n// process\n//', begin)
    source = source[:begin] + Path(__file__).with_name('prepared-resource-mach-notifications.inc').read_text() + '\n' + source[end:]
    files['kqchan.cpp'] = source
    return files
