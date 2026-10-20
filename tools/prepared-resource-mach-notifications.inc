// All Mach transport state is serialized by _sendingMutex. A prior readiness
// signal is flushed before accepting a request; a signal raised during a read
// waits behind its reply. SOCK_SEQPACKET sends are atomic, and EAGAIN retains
// both fixed slots without allocating another queue node or callback.
namespace {
bool preparedProcessPacket(int, const void*, size_t, int, int&) noexcept;
}
bool DarlingServer::Kqchan::MachPort::_flushPreparedMachOutputLocked() noexcept {
    if (!_socket) return true;
    int error = 0;
    if (_preparedMachReplyLength) {
        if (!preparedProcessPacket(_socket->fd(), &_preparedMachReply,
                _preparedMachReplyLength, -1, error)) {
            _preparedMachTransportError.store(error);
            return false;
        }
        _preparedMachReplyLength = 0;
        _deferNotification = false;
    }
    if (_preparedMachNotificationPending && !_deferNotification) {
        dserver_kqchan_call_notification_t notification = {};
        notification.header.number = dserver_kqchan_msgnum_notification;
        if (!preparedProcessPacket(_socket->fd(), &notification, sizeof(notification), -1, error)) {
            _preparedMachTransportError.store(error);
            return false;
        }
        _preparedMachNotificationPending = false;
    }
    _preparedMachTransportError.store(0);
    return true;
}

void DarlingServer::Kqchan::MachPort::_notify() {
    std::unique_lock lock(_sendingMutex);
    if (_canSendNotification) {
        _canSendNotification = false;
        _preparedMachNotificationPending = true;
    }
    (void)_flushPreparedMachOutputLocked();
}

void DarlingServer::Kqchan::MachPort::_completePreparedMachRead(
        const dserver_kqchan_reply_mach_port_read_t& reply) noexcept {
    {
        std::unique_lock lock(_sendingMutex);
        _preparedMachReply.read = reply;
        _preparedMachReplyLength = sizeof(reply);
        _preparedMachRequestInFlight = false;
        (void)_flushPreparedMachOutputLocked();
    }
    // EPOLL_CTL_MOD redelivers existing readiness. An earlier event can have
    // arrived while the request was in flight, with unread bytes still queued.
    if (!Server::sharedInstance().rearmPreparedMonitor(_monitor)) _preparedMachTransportError.store(errno);
}

void DarlingServer::Kqchan::MachPort::_completePreparedMachModify() noexcept {
    {
        std::unique_lock lock(_sendingMutex);
        _preparedMachReply.modify = {};
        _preparedMachReply.modify.header.number = dserver_kqchan_msgnum_mach_port_modify;
        _preparedMachReplyLength = sizeof(_preparedMachReply.modify);
        _preparedMachRequestInFlight = false;
        (void)_flushPreparedMachOutputLocked();
    }
    if (!Server::sharedInstance().rearmPreparedMonitor(_monitor)) _preparedMachTransportError.store(errno);
}

void DarlingServer::Kqchan::MachPort::_modify(uint64_t receiveBuffer, uint64_t receiveBufferSize,
        uint64_t savedFilterFlags, pid_t nstid) {
    auto maybeThread = threadRegistry().lookupEntryByNSID(nstid);
    if (!maybeThread) throw std::runtime_error("No thread for Mach port kqchan modification?");
    auto self = shared_from_this();
    Thread::kernelAsync([self, thread = *maybeThread, receiveBuffer, receiveBufferSize, savedFilterFlags]() {
        Thread::currentThread()->impersonate(thread);
        dtape_kqchan_mach_port_modify(self->_dtapeKqchan, receiveBuffer, receiveBufferSize, savedFilterFlags);
        Thread::currentThread()->impersonate(nullptr);
        // Recheck in this existing kernel context. Deferred readiness follows
        // the reply without an allocating post-send kernelAsync callback.
        if (dtape_kqchan_mach_port_has_events(self->_dtapeKqchan)) self->_notify();
        self->_completePreparedMachModify();
    });
}

void DarlingServer::Kqchan::MachPort::_read(uint64_t defaultBuffer, uint64_t defaultBufferSize, pid_t nstid) {
    auto maybeThread = threadRegistry().lookupEntryByNSID(nstid);
    if (!maybeThread) throw std::runtime_error("No thread for Mach port kqchan read?");
    auto self = shared_from_this();
    Thread::kernelAsync([self, thread = *maybeThread, defaultBuffer, defaultBufferSize]() {
        dserver_kqchan_reply_mach_port_read_t reply = {};
        reply.header.number = dserver_kqchan_msgnum_mach_port_read;
        Thread::currentThread()->impersonate(thread);
        if (!dtape_kqchan_mach_port_fill(self->_dtapeKqchan, &reply, defaultBuffer, defaultBufferSize))
            reply.header.code = 0xdead;
        Thread::currentThread()->impersonate(nullptr);
        if (dtape_kqchan_mach_port_has_events(self->_dtapeKqchan)) self->_notify();
        self->_completePreparedMachRead(reply);
    });
}

bool DarlingServer::Kqchan::MachPort::_processPreparedTransportEvent(Monitor::Event) {
    std::unique_lock lock(_sendingMutex);
    if (!_socket || !_flushPreparedMachOutputLocked() || _preparedMachRequestInFlight) return true;
    union Request {
        dserver_kqchan_call_mach_port_modify_t modify;
        dserver_kqchan_call_mach_port_read_t read;
        Request() : modify{} {}
    } request;
    struct iovec vector = {&request, sizeof(request)};
    struct msghdr header = {};
    header.msg_iov = &vector;
    header.msg_iovlen = 1;
    ssize_t count;
    do { count = recvmsg(_socket->fd(), &header, MSG_DONTWAIT | MSG_CMSG_CLOEXEC); }
    while (count < 0 && errno == EINTR);
    if (count < 0) {
        if (errno != EAGAIN && errno != EWOULDBLOCK) _preparedMachTransportError.store(errno);
        return true;
    }
    if (count == 0) return true;
    if (count < static_cast<ssize_t>(sizeof(dserver_kqchan_callhdr_t)) || (header.msg_flags & MSG_TRUNC))
        throw std::invalid_argument("invalid Mach kqchan request length");
    const auto number = request.modify.header.number;
    if ((number == dserver_kqchan_msgnum_mach_port_modify && count < static_cast<ssize_t>(sizeof(request.modify))) ||
        (number == dserver_kqchan_msgnum_mach_port_read && count < static_cast<ssize_t>(sizeof(request.read))))
        throw std::invalid_argument("short Mach kqchan request");
    if (number != dserver_kqchan_msgnum_mach_port_modify && number != dserver_kqchan_msgnum_mach_port_read)
        throw std::invalid_argument("unknown Mach kqchan request");
    if (number == dserver_kqchan_msgnum_mach_port_read) {
        ++_notificationCount;
        _canSendNotification = true;
    }
    _deferNotification = true;
    _preparedMachRequestInFlight = true;
    lock.unlock();
    if (number == dserver_kqchan_msgnum_mach_port_modify)
        _modify(request.modify.receive_buffer, request.modify.receive_buffer_size,
            request.modify.saved_filter_flags, request.modify.header.tid);
    else _read(request.read.default_buffer, request.read.default_buffer_size, request.read.header.tid);
    return true;
}

void DarlingServer::Kqchan::MachPort::_processMessages() {
    (void)_processPreparedTransportEvent(Monitor::Event::Readable);
}

// These legacy entry points are retained for source compatibility. Transport
// completion no longer installs a send callback or schedules a second job.
void DarlingServer::Kqchan::MachPort::_checkForEventsAsync() {
    if (dtape_kqchan_mach_port_has_events(_dtapeKqchan)) _notify();
}
std::function<void()> DarlingServer::Kqchan::MachPort::_checkForEventsAsyncFactory() {
    return {};
}
