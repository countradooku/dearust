#!/usr/bin/env python3
"""Prepare an exec generation completely before publishing its C handles.

Apply after checked IPC, generation contexts and Process-channel transport.
The guest has already physically exec'd when Checkin arrives. Failure retains
server bookkeeping only for terminal error delivery; it cannot roll back exec.
"""
from pathlib import Path


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('prepared exec anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


CHANNEL = '''
public:
        struct PreparedExecNotification {
            std::shared_ptr<Process> channel;
            std::list<Event> events;
            void commit() noexcept;
            void send() noexcept;
        };
        PreparedExecNotification prepareExecNotification();
private:
'''


CHANNEL_METHODS = '''
DarlingServer::Kqchan::Process::PreparedExecNotification
DarlingServer::Kqchan::Process::prepareExecNotification() {
    PreparedExecNotification prepared;
    prepared.channel = shared_from_this();
    Event event{};
    event.events = NOTE_EXEC;
    event.data = 0;
    prepared.events.emplace_back(std::move(event));
    return prepared;
}
void DarlingServer::Kqchan::Process::PreparedExecNotification::commit() noexcept {
    std::unique_lock lock(channel->_mutex);
    channel->_events.splice(channel->_events.end(), events);
}
void DarlingServer::Kqchan::Process::PreparedExecNotification::send() noexcept {
    channel->_sendPreparedProcessNotification();
}
'''


def patch_prepared_exec(files):
    files = dict(files)
    header = '#include <list>\n' + files['kqchan.hpp']
    header = once(header, '\t\tstd::deque<Event> _events;',
                  CHANNEL + '\t\tstd::list<Event> _events;')
    files['kqchan.hpp'] = header
    files['kqchan.cpp'] += '\n' + CHANNEL_METHODS
    files['process.hpp'] = once(files['process.hpp'], '\t\tvoid notifyCheckin(Architecture architecture);',
        '\t\tvoid notifyCheckin(Architecture architecture);\n        struct PreparedExecRetirement;')
    text = files['process.cpp']
    begin = text.index('void DarlingServer::Process::notifyCheckin(Architecture architecture) {')
    end = text.index('void DarlingServer::Process::setPendingReplacement()', begin)
    body = Path(__file__).with_name('resource-prepared-exec.inc').read_text()
    files['process.cpp'] = text[:begin] + body + '\n' + text[end:]
    return files
