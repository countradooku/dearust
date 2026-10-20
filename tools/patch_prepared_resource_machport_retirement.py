#!/usr/bin/env python3
"""Reserve Mach-port channel retirement and retain weak callback context."""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('prepared Mach-port retirement anchor is not unique: ' + old[:100])
    return text.replace(old, new, 1)


RETIREMENT_TYPE = '''
struct DarlingServer::Kqchan::MachPort::PreparedRetirement {
    PreparedResourceCleanupJob job;
    std::weak_ptr<MachPort> owner;
    dtape_kqchan_mach_port_t* handle = nullptr;
    PreparedRetirement() {
        job.context = this;
        job.run = destroy;
    }
    static void notify(void* context) {
        auto* retirement = static_cast<PreparedRetirement*>(context);
        // The immutable context survives until C detach has drained port and
        // port-set callbacks. Promotion prevents concurrent C++ destruction.
        if (auto channel = retirement->owner.lock()) channel->_notify();
    }
    static void destroy(void* context) noexcept {
        auto* retirement = static_cast<PreparedRetirement*>(context);
        if (retirement->handle) dtape_kqchan_mach_port_destroy(retirement->handle);
        delete retirement;
    }
};
'''

MACH_DESTRUCTOR = '''DarlingServer::Kqchan::MachPort::~MachPort() {
    if (_dtapeKqchan) {
        auto* retirement = _preparedRetirement.release();
        if (!retirement) std::terminate();
        retirement->handle = _dtapeKqchan;
        _dtapeKqchan = nullptr;
        // Only callback publication is disabled here. The context remains valid
        // until kernel-lane detach drains every callback and releases its task.
        dtape_kqchan_mach_port_disable_notifications(retirement->handle);
        Thread::enqueueResourceCleanup(retirement->job);
    }
};
'''


def patch_machport_c(text):
    text = once(text, '''\tif (kqchan->knote.kn_flags & EV_ERROR) {
\t\tfree(kqchan);''', '''\tif (kqchan->knote.kn_flags & EV_ERROR) {
        task_deallocate(&kqchan->task->xnu_task);
\t\tfree(kqchan);''')
    text = once(text, '''\tif (os_ref_release(&kqchan->refcount) != 0) {
\t\tpanic("Duct-taped Mach port kqchan over-retained or still in-use at destruction");
\t}

\tfilt_machportdetach(&kqchan->knote);''', '''    // detach unlinks under the mqueue lock and joins the port-set waiter.
    // Only after that barrier can callback context and task ownership retire.
    filt_machportdetach(&kqchan->knote);
    if (os_ref_release(&kqchan->refcount) != 0) {
        panic("Duct-taped Mach port kqchan over-retained at destruction");
    }''')
    text = once(text, '''\tkqchan->callback = NULL;
\tkqchan->context = NULL;''', '''    __atomic_store_n(&kqchan->callback, NULL, __ATOMIC_RELEASE);
    // Context is immutable after creation; an already-observed callback owns a
    // valid weak C++ context until the final detach barrier has completed.''')
    text = once(text, 'dtape_kqchan_mach_port_notification_callback_f cb = kqchan->callback;',
        'dtape_kqchan_mach_port_notification_callback_f cb = __atomic_load_n(&kqchan->callback, __ATOMIC_ACQUIRE);')
    text = once(text, 'dtape_kqchan_mach_port_notification_callback_f wcb = kqchan->callback;',
        'dtape_kqchan_mach_port_notification_callback_f wcb = __atomic_load_n(&kqchan->callback, __ATOMIC_ACQUIRE);')
    # Optional debug expressions must not race the atomic disable operation.
    text = text.replace('kn, kqchan, kqchan->callback);',
                        'kn, kqchan, __atomic_load_n(&kqchan->callback, __ATOMIC_ACQUIRE));')
    return text


def patch_machport_retirement(files):
    files = dict(files)
    header = files['kqchan.hpp']
    header = once(header, '\t\tdtape_kqchan_mach_port_t* _dtapeKqchan = nullptr;',
        '\t\tdtape_kqchan_mach_port_t* _dtapeKqchan = nullptr;\n'
        '\t\tstruct PreparedRetirement;\n'
        '\t\tstd::unique_ptr<PreparedRetirement> _preparedRetirement;')
    files['kqchan.hpp'] = header
    source = files['kqchan.cpp']
    anchor = 'DarlingServer::Kqchan::MachPort::MachPort('
    source = once(source, anchor, RETIREMENT_TYPE + '\n' + anchor)
    begin = source.index('DarlingServer::Kqchan::MachPort::~MachPort() {')
    end = source.index('uintptr_t DarlingServer::Kqchan::MachPort::_idForProcess()', begin)
    source = source[:begin] + MACH_DESTRUCTOR + '\n' + source[end:]
    begin = source.index('\t// NOTE: the duct-taped kqchan will never notify us after we die')
    end = source.index('\n\tif (!_dtapeKqchan)', begin)
    source = source[:begin] + '''    Thread::requireResourceCleanupReady();
    _preparedRetirement = std::make_unique<PreparedRetirement>();
    _preparedRetirement->owner = weak_from_this();
    {
        std::shared_lock processLock(proc->_rwlock);
        if (proc->_dead || !proc->_dtapeTask)
            throw std::system_error(ESRCH, std::generic_category());
        _dtapeKqchan = dtape_kqchan_mach_port_create(proc->_dtapeTask, _port,
            _receiveBuffer, _receiveBufferSize, _savedFilterFlags,
            PreparedRetirement::notify, _preparedRetirement.get());
    }
''' + source[end:]
    # Hold the returned descriptor across notification preparation, which can
    # throw in the still-separate ordinary Mach message/read path.
    begin = source.index('int DarlingServer::Kqchan::MachPort::setup() {')
    end = source.index('void DarlingServer::Kqchan::MachPort::_processMessages()', begin)
    setup = source[begin:end]
    setup = once(setup, '\tint fd = Kqchan::setup();', '\tFD fd(Kqchan::setup());')
    setup = once(setup, '\treturn fd;', '\treturn fd.extract();')
    source = source[:begin] + setup + source[end:]
    files['kqchan.cpp'] = source
    if 'kqchan.c' in files:
        files['kqchan.c'] = patch_machport_c(files['kqchan.c'])
    return files
