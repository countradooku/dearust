#!/usr/bin/env python3
"""Contain prepared-checkin failures with startup-owned reply storage.

Only the server's serial inbox thread writes the one pending reply slot. Inbox
dispatch pauses while it is pending; monitor/timer dispatch remains available.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('prepared checkin anchor is not unique: ' + old[:100])
    return text.replace(old, new, 1)


SERVER_FIELDS = '''
        // Constructed during server startup, before accepting guest calls.
        Message _preparedCheckinFailure{sizeof(dserver_rpc_reply_checkin_t), 0};
        bool _preparedCheckinFailurePending = false;
        bool _flushPreparedCheckinFailure();
'''

SERVER_METHODS = '''
void DarlingServer::Server::queuePreparedCheckinFailure(Address address, int error) noexcept {
    if (_preparedCheckinFailurePending) std::terminate();
    _preparedCheckinFailure.setAddress(address);
    auto* body = reinterpret_cast<dserver_rpc_reply_checkin_t*>(_preparedCheckinFailure.data().data());
    *body = {};
    body->header.number = dserver_callnum_checkin;
    body->header.code = -error;
    _preparedCheckinFailurePending = true;
}

bool DarlingServer::Server::_flushPreparedCheckinFailure() {
    if (!_preparedCheckinFailurePending) return true;
    ssize_t result;
    do {
        result = sendmsg(_listenerSocket, &_preparedCheckinFailure.rawHeader(), MSG_DONTWAIT | MSG_NOSIGNAL);
    } while (result < 0 && errno == EINTR);
    if (result < 0) {
        const int error = errno;
        if (error == EAGAIN || error == EWOULDBLOCK) return false;
        if (error == EPIPE || error == ECONNREFUSED || error == ENOENT) {
            // Match MessageQueue::sendMany: a peer that has gone away cannot
            // receive its reply. Retire that message and continue other work.
            _preparedCheckinFailurePending = false;
            return true;
        }
        // Preserve the event loop's existing terminal transport-error behavior.
        // This is separate from containment of registration/preparation failures.
        throw std::system_error(error, std::generic_category(), "send prepared checkin failure");
    }
    if (static_cast<size_t>(result) != _preparedCheckinFailure.data().size())
        throw std::runtime_error("short prepared checkin datagram");
    _preparedCheckinFailurePending = false;
    return true;
}
'''

CHECKIN_CATCHES = '''
    } catch (const std::bad_alloc&) {
        if (!preparedIsCheckin) throw;
        Server::sharedInstance().queuePreparedCheckinFailure(preparedCheckinAddress, ENOMEM);
        return nullptr;
    } catch (const std::system_error& error) {
        if (!preparedIsCheckin) throw;
        const auto& category = error.code().category();
        const int code = (&category == &std::generic_category() || &category == &std::system_category())
            ? error.code().value() : EIO;
        Server::sharedInstance().queuePreparedCheckinFailure(preparedCheckinAddress, code);
        return nullptr;
    }
'''


def patch_checkin(files):
    files = dict(files)
    header = files['server.hpp']
    header = once(header, '\t\tvoid _worker(std::shared_ptr<Thread> thread);',
                  SERVER_FIELDS + '\t\tvoid _worker(std::shared_ptr<Thread> thread);')
    header = once(header, '\t\tvoid sendMessage(Message&& message);',
                  '\t\tvoid sendMessage(Message&& message);\n'
                  '\t\tvoid queuePreparedCheckinFailure(Address address, int error) noexcept;')
    files['server.hpp'] = header
    server = files['server.cpp']
    server = once(server, '\t\tif (_canRead) {\n\t\t\t_canRead = _inbox.receiveMany(_listenerSocket);',
        '\t\tif (!_preparedCheckinFailurePending && (_canRead || !_inbox.empty())) {\n'
        '\t\t\tif (_canRead) _canRead = _inbox.receiveMany(_listenerSocket);')
    server = once(server, '\t\t\twhile (auto msg = _inbox.pop()) {',
        '\t\t\twhile (!_preparedCheckinFailurePending) {\n'
        '\t\t\t\tauto msg = _inbox.pop();\n\t\t\t\tif (!msg) break;')
    server = once(server, '''\t\tif (_canWrite || !_outbox.empty()) {
\t\t\t_canWrite = _outbox.sendMany(_listenerSocket);
\t\t}''', '''\t\tif (_canWrite || !_outbox.empty()) {
\t\t\t_canWrite = _outbox.sendMany(_listenerSocket);
\t\t}
        // A prepared error cannot overtake replies already queued by workers.
        // EAGAIN retains the slot without allocation; socket EPOLLOUT retries it.
        if (_preparedCheckinFailurePending && _outbox.empty())
            _canWrite = _flushPreparedCheckinFailure();''')
    server = once(server, '\t\tstruct epoll_event events[16];',
        '        // receiveMany may already have buffered later calls. Resume them\n'
        '        // immediately after the error is sent, without a new socket edge.\n'
        '        if (!_preparedCheckinFailurePending && !_inbox.empty()) continue;\n\n'
        '\t\tstruct epoll_event events[16];')
    files['server.cpp'] = server + '\n' + SERVER_METHODS
    call = files['call.cpp']
    call = once(call, '\t\t\t} catch (std::system_error e) {\n\t\t\t\treturn tmp;',
        '\t\t\t} catch (const std::system_error& e) {\n'
        '\t\t\t\tif (header->number == dserver_callnum_checkin) throw;\n\t\t\t\treturn tmp;')
    begin = call.index('\t\t\t\tif (e.code().value() == EAGAIN && header->number == dserver_callnum_checkin) {')
    end = call.index('\t\t\t\tcallLog.error()', begin)
    call = call[:begin] + '\t\t\t\tif (header->number == dserver_callnum_checkin) throw;\n' + call[end:]
    call = once(call, '\t\tbool threadRetry = false;\n', '')
    call = once(call, '\t\t\tif (threadRetry) return nullptr;\n', '')
    begin = call.index('\tif ((header->number & DSERVER_CALL_UNMANAGED_FLAG) == 0) {')
    call = call[:begin] + '''    const bool preparedIsCheckin = header->number == dserver_callnum_checkin;
    const auto preparedCheckinAddress = requestMessage.address();
    try {
''' + call[begin:]
    end = call.index('\n\tif (callLog.', begin)
    call = call[:end] + '\n' + CHECKIN_CATCHES + call[end:]
    files['call.cpp'] = call
    return files
