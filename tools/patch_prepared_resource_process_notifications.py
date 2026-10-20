#!/usr/bin/env python3
"""Owned allocation-free Process exit fanout and process-kqchan transport."""
from pathlib import Path
import re


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('prepared process notification anchor is not unique: ' + old[:100])
    return text.replace(old, new, 1)


KQ_FIELDS = '''
        // A Process can exit only once. Its inline terminal event keeps its
        // original position relative to the ordinary, potentially growing queue.
        Event _preparedExitEvent{};
        size_t _preparedEventsBeforeExit = 0;
        bool _preparedExitRecorded = false;
        bool _preparedExitPending = false;
        union PreparedReply {
            dserver_kqchan_reply_proc_modify_t modify;
            dserver_kqchan_reply_proc_read_t read;
            PreparedReply(): read{} {}
        } _preparedReply;
        size_t _preparedReplyLength = 0;
        int _preparedReplyFD = -1;
        bool _preparedNotificationPending = false;
        std::atomic<int> _preparedTransportError{0};
        void _notifyPreparedExit(int64_t data) noexcept;
        bool _takePreparedProcessEvent(Event& event, uint32_t& flags);
        void _prepareProcessNotificationLocked() noexcept;
        bool _flushPreparedProcessOutputLocked() noexcept;
        void _sendPreparedProcessNotification() noexcept;
        bool _processPreparedTransportEvent(Monitor::Event event) override;
public:
        int preparedTransportError() const noexcept { return _preparedTransportError.load(); }
private:
'''

PROCESS_METHODS = '''
void DarlingServer::Process::notifyPreparedExitListeners() noexcept {
    decltype(_listeningKqchannels) listeners;
    {
        std::unique_lock lock(_rwlock);
        listeners.swap(_listeningKqchannels);
    }
    // Destruction/subscription can acquire Process locks. Never retain our
    // process lock while touching a channel, including self-subscriptions.
    for (auto& item: listeners)
        if (auto channel = item.second.lock()) channel->_notifyPreparedExit(0);
}
'''


def replace_function(text, signature, next_signature, replacement):
    begin = text.index(signature)
    end = text.index(next_signature, begin)
    return text[:begin] + replacement + '\n\n' + text[end:]


def patch_process_notifications(files):
    files = dict(files)
    ph = files['process.hpp']
    ph = once(ph, '\t\tvoid notifyDead();',
              '\t\tvoid notifyDead();\n\t\tvoid notifyPreparedExitListeners() noexcept;')
    files['process.hpp'] = ph
    ps = files['process.cpp']
    ps = replace_function(ps, 'void DarlingServer::Process::registerListeningKqchan(',
        'void DarlingServer::Process::unregisterListeningKqchan(', '''void DarlingServer::Process::registerListeningKqchan(std::shared_ptr<Kqchan::Process> channel) {
    {
        std::unique_lock lock(_rwlock);
        if (!_dead) {
            _listeningKqchannels[channel->_idForProcess()] = channel;
            return;
        }
    }
    // An attachment racing death must observe the terminal event as well.
    channel->_notifyPreparedExit(0);
};''')
    files['process.cpp'] = ps + '\n' + PROCESS_METHODS
    kh = files['kqchan.hpp']
    kh = once(kh, '#include <deque>', '#include <deque>\n#include <atomic>\n#include <darlingserver/rpc-supplement.h>')
    kh = once(kh, '\t\tvirtual void _processMessages();\n\n\t\tvirtual std::shared_ptr<Kqchan> sharedFromRoot();',
        '\t\tvirtual void _processMessages();\n'
        '\t\tvirtual bool _processPreparedTransportEvent(Monitor::Event) { return false; }\n\n'
        '\t\tvirtual std::shared_ptr<Kqchan> sharedFromRoot();')
    kh = once(kh, '\t\tstd::deque<Event> _events;', '\t\tstd::deque<Event> _events;\n' + KQ_FIELDS)
    files['kqchan.hpp'] = kh
    ks = files['kqchan.cpp']
    ks = once(ks, '\tkqchanLog.debug() << *this << ": Destroying kqchan" << kqchanLog.endLog;',
        '\tif (_monitor) Server::sharedInstance().removeMonitor(_monitor);')
    ks = once(ks, '\t_socket = std::make_shared<FD>(fds[0]);',
        '\tFD ownedSocket(fds[0]);\n\tFD peerSocket(fds[1]);\n'
        '\t_socket = std::make_shared<FD>(std::move(ownedSocket));')
    ks = once(ks, '\treturn fds[1];', '\treturn peerSocket.extract();')
    begin = ks.index('\t_monitor = std::make_shared<Monitor>')
    end = ks.index('\tDarlingServer::Server::sharedInstance().addMonitor(_monitor);', begin)
    callback = ks[begin:end]
    callback = re.sub(r'^\s*kqchanLog\.[^\n]*\n', '', callback, flags=re.MULTILINE)
    callback = once(callback, '\t\tif (static_cast<uint64_t>(event & Monitor::Event::Readable) != 0) {',
        '\t\tif (self->_processPreparedTransportEvent(event)) return;\n\n'
        '\t\tif (static_cast<uint64_t>(event & Monitor::Event::Readable) != 0) {')
    ks = ks[:begin] + callback + ks[end:]
    ks = once(ks, '\tkqchanProcLog.debug() << *this << ": Destroying process kqchan" << kqchanProcLog.endLog;',
        '\tif (_preparedReplyFD >= 0) close(_preparedReplyFD);')
    begin = ks.index('int DarlingServer::Kqchan::Process::setup() {')
    end = ks.index('void DarlingServer::Kqchan::Process::_processMessages()', begin)
    setup = ks[begin:end]
    setup = replace_function(setup, '\t{\n\t\tstd::unique_lock lock(_mutex);', '\n\treturn fd;', '''    bool attach;
    {
        std::unique_lock lock(_mutex);
        attach = !_attached;
    }
    if (attach) {
        targetProcess->registerListeningKqchan(shared_from_this());
        std::unique_lock lock(_mutex);
        _attached = true;
    }
    _sendPreparedProcessNotification();''')
    ks = ks[:begin] + setup + ks[end:]
    ks = replace_function(ks, 'void DarlingServer::Kqchan::Process::_processMessages() {',
        'void DarlingServer::Kqchan::Process::_modify(', '''void DarlingServer::Kqchan::Process::_processMessages() {
    (void)_processPreparedTransportEvent(Monitor::Event::Readable);
};''')
    # The fixed transport below owns replies and acknowledgements directly.
    begin = ks.index('void DarlingServer::Kqchan::Process::_modify(')
    end = ks.index('void DarlingServer::Kqchan::Process::_notify(', begin)
    ks = ks[:begin] + ks[end:]
    ks = once(ks, 'void DarlingServer::Kqchan::Process::_notify(uint32_t event, int64_t data) {',
        'void DarlingServer::Kqchan::Process::_notify(uint32_t event, int64_t data) {\n'
        '\tif (event == NOTE_EXIT) { _notifyPreparedExit(data); return; }')
    ks = once(ks, '''\t\t_events.push_back(std::move(newEvent));
\t\tif (_socket) {
\t\t\t_sendNotification();
\t\t}
\t}''', '''\t\t_events.push_back(std::move(newEvent));
\t}
    _sendPreparedProcessNotification();''')
    ks = replace_function(ks, 'void DarlingServer::Kqchan::Process::_checkForEventsAsync() {',
        'std::function<void()> DarlingServer::Kqchan::Process::_checkForEventsAsyncFactory()',
        '''void DarlingServer::Kqchan::Process::_checkForEventsAsync() {
    _sendPreparedProcessNotification();
};''')
    methods = Path(__file__).with_name('prepared-resource-process-notifications.inc').read_text()
    files['kqchan.cpp'] = ks + '\n' + methods
    return files
