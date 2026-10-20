#!/usr/bin/env python3
"""Prepare owned server publication without exposing half-registered objects.

Apply after the kernel-handle ownership transform. Registry must reserve its
allocating storage before preparePublication(), then commit without allocation.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('prepared publication anchor is not unique: ' + old[:100])
    return text.replace(old, new, 1)


PROCESS_DECLARATIONS = '''
private:
    std::unique_ptr<PreparedProcessMonitor> _preparedPublicationMonitor;
    bool _publicationCommitted = false;
public:
    void preparePublication();
    void commitPublication() noexcept;
    void abortPublication() noexcept;
'''

THREAD_DECLARATIONS = '''
private:
    std::unique_lock<std::shared_mutex> _publicationProcessLock;
    std::weak_ptr<Thread> _publicationIdentity;
    std::weak_ptr<Thread> _publicationPrevious;
    bool _publicationPrepared = false;
    bool _publicationHadPrevious = false;
    bool _publicationCommitted = false;
public:
    void preparePublication();
    void commitPublication() noexcept;
    void abortPublication() noexcept;
'''

MONITOR_DECLARATION = '''
    // The event loop takes _monitorsLock before promoting an epoll raw pointer.
    // Holding it from ADD until registry commit makes callbacks invisible until
    // every public index is coherent. The token owns all rollback storage.
    class PreparedProcessMonitor {
        friend class Server;
        Server* _server;
        std::shared_ptr<Monitor> _monitor;
        std::unique_lock<std::mutex> _registryLock;
        std::unique_lock<std::mutex> _descriptorLock;
        bool _added = false;
        explicit PreparedProcessMonitor(Server* server): _server(server) {}
    public:
        ~PreparedProcessMonitor() noexcept;
        void commit() noexcept;
        void abort() noexcept;
    };
'''

PROCESS_METHODS = '''
void DarlingServer::Process::preparePublication() {
    if (_publicationCommitted || _preparedPublicationMonitor) return;
    // The kernel Process has no Linux pidfd and is not a guest monitor target.
    if (_pid < 0) return;
    _preparedPublicationMonitor = Server::sharedInstance().prepareProcessMonitor(shared_from_this());
}

void DarlingServer::Process::commitPublication() noexcept {
    _publicationCommitted = true;
    if (_preparedPublicationMonitor) _preparedPublicationMonitor->commit();
    _preparedPublicationMonitor.reset();
}

void DarlingServer::Process::abortPublication() noexcept {
    _preparedPublicationMonitor.reset();
}
'''

THREAD_METHODS = '''
void DarlingServer::Thread::preparePublication() {
    if (_publicationCommitted || _publicationPrepared) return;
    _publicationProcessLock = std::unique_lock<std::shared_mutex>(_process->_rwlock);
    try {
        if (_process->_dead)
            throw std::system_error(ESRCH, std::generic_category(), "process died before thread publication");
        _publicationIdentity = shared_from_this();
        auto found = _process->_threads.find(_nstid);
        _publicationHadPrevious = found != _process->_threads.end();
        if (_publicationHadPrevious) {
            _publicationPrevious = found->second;
            found->second = _publicationIdentity;
        } else {
            // Allocation belongs to preparation. Process readers remain excluded
            // until the global thread Registry has committed all its indexes.
            _process->_threads.emplace(_nstid, _publicationIdentity);
        }
        _publicationPrepared = true;
    } catch (...) {
        _publicationProcessLock.unlock();
        throw;
    }
}

void DarlingServer::Thread::commitPublication() noexcept {
    if (_publicationCommitted) return;
    if (!_publicationPrepared) std::terminate();
    _publicationCommitted = true;
    _publicationPrepared = false;
    _publicationPrevious.reset();
    _publicationProcessLock.unlock();
}

void DarlingServer::Thread::abortPublication() noexcept {
    if (!_publicationPrepared) return;
    auto found = _process->_threads.find(_nstid);
    if (found != _process->_threads.end() &&
        !found->second.owner_before(_publicationIdentity) &&
        !_publicationIdentity.owner_before(found->second)) {
        if (_publicationHadPrevious) found->second = _publicationPrevious;
        else _process->_threads.erase(found);
    }
    _publicationPrepared = false;
    _publicationPrevious.reset();
    _publicationProcessLock.unlock();
}

void DarlingServer::Thread::registerWithProcess() {
    preparePublication();
    commitPublication();
}
'''

THREAD_DESTRUCTOR = '''DarlingServer::Thread::~Thread() noexcept {
    abortPublication();
    if (_stack.isValid()) {
        // StackPool::free may allocate when it caches a stack. Final destruction
        // must work under exhaustion, so release this remaining mapping directly.
        const auto pageSize = static_cast<size_t>(getpagesize());
        auto base = static_cast<char*>(_stack.base);
        const auto size = _stack.size + (_stack.usesGuardPages ? 2 * pageSize : 0);
        if (_stack.usesGuardPages) base -= pageSize;
        if (munmap(base, size) < 0) std::terminate();
        _stack = StackPool::Stack();
    }
    if (!_process || !_publicationCommitted) return;
    std::unique_lock lock(_process->_rwlock);
    auto found = _process->_threads.find(_nstid);
    // owner_before compares control-block identity even during destruction,
    // when weak_ptr::lock() can no longer promote this object's last owner.
    if (found != _process->_threads.end() &&
        !found->second.owner_before(_publicationIdentity) &&
        !_publicationIdentity.owner_before(found->second))
        _process->_threads.erase(found);
    // Guest death is authoritative through the required real-pidfd monitor.
    // Failed registration or mere Thread destruction must never simulate death.
};
'''

MONITOR_METHODS = '''
std::unique_ptr<DarlingServer::PreparedProcessMonitor>
DarlingServer::Server::prepareProcessMonitor(std::shared_ptr<Process> process) {
    auto token = std::unique_ptr<PreparedProcessMonitor>(new PreparedProcessMonitor(this));
    std::weak_ptr<Process> weakProcess = process;
    token->_monitor = std::make_shared<Monitor>(process->_pidfd,
        Monitor::Event::Readable | Monitor::Event::HangUp, false, false,
        [this, weakProcess](std::shared_ptr<Monitor> monitor, Monitor::Event) {
            removeMonitor(monitor);
            if (auto process = weakProcess.lock()) process->notifyDead();
        });
    token->_registryLock = std::unique_lock<std::mutex>(_monitorsLock);
    token->_descriptorLock = std::unique_lock<std::mutex>(token->_monitor->_lock);
    const auto maximum = _monitors.max_size();
    if (_monitors.size() >= maximum ||
        _monitorsWaitingToDie.size() > maximum - _monitors.size() - 1)
        throw std::length_error("process monitor capacity exhausted");
    const auto grow = [](auto& entries, size_t required) {
        if (required <= entries.capacity()) return;
        const auto limit = entries.max_size();
        auto capacity = entries.capacity();
        capacity = capacity <= limit / 2 ? capacity * 2 : limit;
        if (capacity < required) capacity = required;
        entries.reserve(capacity);
    };
    grow(_monitors, _monitors.size() + 1);
    grow(_monitorsWaitingToDie, _monitorsWaitingToDie.size() + _monitors.size() + 1);
    _monitors.push_back(token->_monitor);
    struct epoll_event settings = {};
    settings.data.ptr = token->_monitor.get();
    settings.events = token->_monitor->_events;
    int result;
    do { result = epoll_ctl(_epollFD, EPOLL_CTL_ADD, token->_monitor->_fd->fd(), &settings); }
    while (result < 0 && errno == EINTR);
    if (result < 0) {
        const int error = errno;
        _monitors.pop_back();
        throw std::system_error(error, std::generic_category(), "prepare process monitor");
    }
    token->_added = true;
    return token;
}

void DarlingServer::Server::_commitPreparedProcessMonitor(PreparedProcessMonitor& token) noexcept {
    if (!token._added) return;
    token._monitor->_server = this;
    token._added = false;
    token._descriptorLock.unlock();
    token._registryLock.unlock();
}

void DarlingServer::Server::_abortPreparedProcessMonitor(PreparedProcessMonitor& token) noexcept {
    if (!token._added) return;
    int result;
    do { result = epoll_ctl(_epollFD, EPOLL_CTL_DEL, token._monitor->_fd->fd(), nullptr); }
    while (result < 0 && errno == EINTR);
    // ENOENT/EBADF mean there is no surviving registration for this descriptor.
    // Other failures with owned live epoll/pidfd handles violate an invariant.
    if (result < 0 && errno != ENOENT && errno != EBADF) std::terminate();
    // epoll_wait may already have obtained this pointer before waiting for our
    // lock. Retain its address until that event-loop iteration has completed.
    _monitorsWaitingToDie.push_back(token._monitor);
    if (_monitors.empty() || _monitors.back() != token._monitor) std::terminate();
    _monitors.pop_back();
    token._monitor->_server = nullptr;
    token._added = false;
    token._descriptorLock.unlock();
    token._registryLock.unlock();
    do { result = eventfd_write(_wakeupFD, 1); } while (result < 0 && errno == EINTR);
    if (result < 0 && errno != EAGAIN) std::terminate();
}

DarlingServer::PreparedProcessMonitor::~PreparedProcessMonitor() noexcept { abort(); }
void DarlingServer::PreparedProcessMonitor::commit() noexcept { _server->_commitPreparedProcessMonitor(*this); }
void DarlingServer::PreparedProcessMonitor::abort() noexcept { _server->_abortPreparedProcessMonitor(*this); }
'''


def patch_publication(files):
    """Return transformed source strings; never read/write the reference tree."""
    files = dict(files)
    ph = files['process.hpp']
    ph = once(ph, '\tclass Call;', '\tclass Call;\n\tclass PreparedProcessMonitor;')
    files['process.hpp'] = once(ph, '\n\t};\n};\n', '\n' + PROCESS_DECLARATIONS + '\n\t};\n};\n')
    th = files['thread.hpp']
    th = once(th, '~Thread() noexcept(false);', '~Thread() noexcept;')
    files['thread.hpp'] = once(th, '\n\t};\n};\n', '\n' + THREAD_DECLARATIONS + '\n\t};\n};\n')
    sh = files['server.hpp']
    sh = once(sh, '\tclass Server {', '\tclass PreparedProcessMonitor;\n\tclass Server {\n\t\tfriend class PreparedProcessMonitor;')
    sh = once(sh, '\t\tvoid _worker(std::shared_ptr<Thread> thread);',
              '\t\tvoid _worker(std::shared_ptr<Thread> thread);\n'
              '\t\tvoid _commitPreparedProcessMonitor(PreparedProcessMonitor&) noexcept;\n'
              '\t\tvoid _abortPreparedProcessMonitor(PreparedProcessMonitor&) noexcept;')
    sh = once(sh, '\t\tvoid monitorProcess(std::shared_ptr<Process> process);',
              '\t\tvoid monitorProcess(std::shared_ptr<Process> process);\n'
              '\t\tstd::unique_ptr<PreparedProcessMonitor> prepareProcessMonitor(std::shared_ptr<Process> process);')
    files['server.hpp'] = once(sh, '\n};\n\n#endif', '\n' + MONITOR_DECLARATION + '\n};\n\n#endif')
    ps = files['process.cpp']
    ps = once(ps, '#include <darlingserver/process.hpp>',
              '#include <darlingserver/process.hpp>\n#include <darlingserver/server.hpp>')
    ps = once(ps, '\tprocessLog.info() << *this << ": process being destroyed" << processLog.endLog;',
              '\tabortPublication();')
    files['process.cpp'] = ps + '\n' + PROCESS_METHODS
    ts = files['thread.cpp']
    begin = ts.index('void DarlingServer::Thread::registerWithProcess() {')
    end = ts.index('DarlingServer::Thread::ID DarlingServer::Thread::id() const', begin)
    files['thread.cpp'] = ts[:begin] + THREAD_METHODS + '\n' + THREAD_DESTRUCTOR + '\n' + ts[end:]
    files['server.cpp'] += '\n' + MONITOR_METHODS
    return files
