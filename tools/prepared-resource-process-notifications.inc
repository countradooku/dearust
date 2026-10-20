// Included only in owned kqchan.cpp. Process channels use bounded socket
// backpressure, retaining exactly one current reply and one readiness signal.
namespace {
bool preparedProcessPacket(int socket, const void* bytes, size_t size, int descriptor,
        int& error) noexcept {
    struct iovec vector = {const_cast<void*>(bytes), size};
    alignas(struct cmsghdr) unsigned char control[
        CMSG_SPACE(sizeof(struct ucred)) + CMSG_SPACE(sizeof(int))] = {};
    struct msghdr header = {};
    header.msg_iov = &vector;
    header.msg_iovlen = 1;
    header.msg_control = control;
    header.msg_controllen = CMSG_SPACE(sizeof(struct ucred));
    auto* credentials = CMSG_FIRSTHDR(&header);
    credentials->cmsg_level = SOL_SOCKET;
    credentials->cmsg_type = SCM_CREDENTIALS;
    credentials->cmsg_len = CMSG_LEN(sizeof(struct ucred));
    const struct ucred identity = {getpid(), getuid(), getgid()};
    memcpy(CMSG_DATA(credentials), &identity, sizeof(identity));
    if (descriptor >= 0) {
        header.msg_controllen += CMSG_SPACE(sizeof(int));
        auto* rights = CMSG_NXTHDR(&header, credentials);
        rights->cmsg_level = SOL_SOCKET;
        rights->cmsg_type = SCM_RIGHTS;
        rights->cmsg_len = CMSG_LEN(sizeof(int));
        memcpy(CMSG_DATA(rights), &descriptor, sizeof(descriptor));
    }
    ssize_t sent;
    do { sent = sendmsg(socket, &header, MSG_DONTWAIT | MSG_NOSIGNAL); }
    while (sent < 0 && errno == EINTR);
    if (sent < 0) { error = errno; return false; }
    if (static_cast<size_t>(sent) != size) { error = EIO; return false; }
    error = 0;
    return true;
}
}

void DarlingServer::Kqchan::Process::_notifyPreparedExit(int64_t data) noexcept {
    {
        std::unique_lock lock(_mutex);
        if (_preparedExitRecorded) return;
        _preparedExitEvent.events = NOTE_EXIT;
        _preparedExitEvent.data = data;
        _preparedEventsBeforeExit = _events.size();
        _preparedExitRecorded = true;
        _preparedExitPending = true;
    }
    _sendPreparedProcessNotification();
}

bool DarlingServer::Kqchan::Process::_takePreparedProcessEvent(Event& event, uint32_t& flags) {
    std::unique_lock lock(_mutex);
    flags = _flags;
    if (_preparedExitPending && _preparedEventsBeforeExit == 0) {
        event = std::move(_preparedExitEvent);
        _preparedExitPending = false;
        return true;
    }
    if (_events.empty()) return false;
    event = std::move(_events.front());
    _events.pop_front();
    if (_preparedExitPending && _preparedEventsBeforeExit) --_preparedEventsBeforeExit;
    return true;
}

void DarlingServer::Kqchan::Process::_prepareProcessNotificationLocked() noexcept {
    if (!_canSendNotification || _deferNotification) return;
    std::unique_lock lock(_mutex);
    if (_events.empty() && !_preparedExitPending) return;
    _canSendNotification = false;
    _preparedNotificationPending = true;
}

bool DarlingServer::Kqchan::Process::_flushPreparedProcessOutputLocked() noexcept {
    if (!_socket) return true;
    int error = 0;
    if (_preparedReplyLength) {
        if (!preparedProcessPacket(_socket->fd(), &_preparedReply,
                _preparedReplyLength, _preparedReplyFD, error)) {
            _preparedTransportError.store(error);
            return false;
        }
        if (_preparedReplyFD >= 0) close(_preparedReplyFD);
        _preparedReplyFD = -1;
        _preparedReplyLength = 0;
        _deferNotification = false;
        _prepareProcessNotificationLocked();
    }
    if (_preparedNotificationPending) {
        dserver_kqchan_call_notification_t notification = {};
        notification.header.number = dserver_kqchan_msgnum_notification;
        if (!preparedProcessPacket(_socket->fd(), &notification,
                sizeof(notification), -1, error)) {
            _preparedTransportError.store(error);
            return false;
        }
        _preparedNotificationPending = false;
    }
    _preparedTransportError.store(0);
    return true;
}

void DarlingServer::Kqchan::Process::_sendPreparedProcessNotification() noexcept {
    std::unique_lock lock(_sendingMutex);
    if (!_socket) return;
    _prepareProcessNotificationLocked();
    (void)_flushPreparedProcessOutputLocked();
}

void DarlingServer::Kqchan::Process::_modify(uint32_t flags) {
    {
        std::unique_lock lock(_mutex);
        _flags = flags;
    }
    _preparedReply.modify = {};
    auto* reply = &_preparedReply.modify;
    reply->header.number = dserver_kqchan_msgnum_proc_modify;
    _preparedReplyLength = sizeof(*reply);
    _deferNotification = true;
}

void DarlingServer::Kqchan::Process::_read() {
    auto listeningProcess = _process.lock();
    if (!listeningProcess) return;
    _canSendNotification = true;
    ++_notificationCount;
    _deferNotification = true;
    _preparedReply.read = {};
    auto* reply = &_preparedReply.read;
    reply->header.number = dserver_kqchan_msgnum_proc_read;
    _preparedReplyLength = sizeof(*reply);
    for (;;) {
        Event event;
        uint32_t savedFlags;
        if (!_takePreparedProcessEvent(event, savedFlags)) {
            reply->header.code = 0xdead;
            break;
        }
        reply->data = event.data;
        reply->fflags = event.events & savedFlags;
        if (!reply->fflags) continue;
        if (savedFlags & NOTE_TRACK) {
            if (event.newKqchan) {
                try {
                    FD socket(event.newKqchan->setup());
                    {
                        std::unique_lock lock(event.newKqchan->_mutex);
                        event.newKqchan->_flags = savedFlags;
                    }
                    listeningProcess->registerKqchan(event.newKqchan);
                    _preparedReplyFD = socket.extract();
                } catch (...) {
                    reply->fflags |= NOTE_TRACKERR;
                }
            } else if (event.events & NOTE_FORK) {
                reply->fflags |= NOTE_TRACKERR;
            }
        }
        break;
    }
}

bool DarlingServer::Kqchan::Process::_processPreparedTransportEvent(Monitor::Event) {
    std::unique_lock lock(_sendingMutex);
    if (!_socket || !_flushPreparedProcessOutputLocked()) return true;
    for (;;) {
        dserver_kqchan_call_proc_modify_t request = {};
        struct iovec vector = {&request, sizeof(request)};
        struct msghdr header = {};
        header.msg_iov = &vector;
        header.msg_iovlen = 1;
        ssize_t count;
        do { count = recvmsg(_socket->fd(), &header, MSG_DONTWAIT | MSG_CMSG_CLOEXEC); }
        while (count < 0 && errno == EINTR);
        if (count < 0) {
            if (errno != EAGAIN && errno != EWOULDBLOCK) _preparedTransportError.store(errno);
            return true;
        }
        if (count == 0) return true;
        if (count < static_cast<ssize_t>(sizeof(dserver_kqchan_callhdr_t)) ||
            (header.msg_flags & MSG_TRUNC))
            throw std::invalid_argument("invalid process kqchan request length");
        if (request.header.number == dserver_kqchan_msgnum_proc_modify) {
            if (count < static_cast<ssize_t>(sizeof(dserver_kqchan_call_proc_modify_t)))
                throw std::invalid_argument("short process kqchan modification");
            _modify(request.flags);
        } else if (request.header.number == dserver_kqchan_msgnum_proc_read) {
            _read();
        } else {
            throw std::invalid_argument("unknown process kqchan request");
        }
        if (!_flushPreparedProcessOutputLocked()) return true;
    }
}
