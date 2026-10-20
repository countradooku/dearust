#!/usr/bin/env python3
"""Pin task ownership without holding Process locks across kernel IPC setup.

Run after lifecycle/Mach transforms. Peer descriptors remain RAII-owned through
setup, Process publication and the reply's descriptor insertion boundary.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('kq ownership anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


def patch_kq_ownership(files):
    files = dict(files)
    files['kqchan.cpp'] = once(files['kqchan.cpp'], '''    {
        std::shared_lock processLock(proc->_rwlock);
        if (proc->_dead || !proc->_dtapeTask)
            throw std::system_error(ESRCH, std::generic_category());
        _dtapeKqchan = dtape_kqchan_mach_port_create(proc->_dtapeTask, _port,
            _receiveBuffer, _receiveBufferSize, _savedFilterFlags,
            PreparedRetirement::notify, _preparedRetirement.get());
    }''', '''    dtape_task_t* borrowedTask;
    {
        std::shared_lock processLock(proc->_rwlock);
        borrowedTask = proc->_dtapeTask.load();
        if (proc->_dead || proc->_resourceCleanupQueued || !borrowedTask)
            throw std::system_error(ESRCH, std::generic_category());
        dtape_task_retain(borrowedTask);
    }
    // Port-set attachment creates a kernel Thread. Its publication can acquire
    // this same Process lock, so IPC creation must run after dropping our lock.
    // C creation never throws C++ exceptions; both outcomes release this borrow.
    _dtapeKqchan = dtape_kqchan_mach_port_create(borrowedTask, _port,
        _receiveBuffer, _receiveBufferSize, _savedFilterFlags,
        PreparedRetirement::notify, _preparedRetirement.get());
    dtape_task_release(borrowedTask);''')
    files['process.cpp'] = once(files['process.cpp'], '''void DarlingServer::Process::registerKqchan(std::shared_ptr<Kqchan> kqchan) {
	std::unique_lock lock(_rwlock);

	_kqchannels[kqchan->_idForProcess()] = kqchan;
};''', '''void DarlingServer::Process::registerKqchan(std::shared_ptr<Kqchan> kqchan) {
    std::unique_lock lock(_rwlock);
    if (_dead || _resourceCleanupQueued)
        throw std::system_error(ESRCH, std::generic_category());
    _kqchannels[kqchan->_idForProcess()] = kqchan;
};''')
    text = files['call.cpp']
    for name, cls, args, wire in (
            ('KqchanMachPortOpen', 'MachPort', '_body.port_name, _body.receive_buffer, _body.receive_buffer_size, _body.saved_filter_flags', 'kqchan_mach_port_open'),
            ('KqchanProcOpen', 'Process', '_body.pid, _body.flags', 'kqchan_proc_open')):
        begin = text.index('void DarlingServer::Call::' + name + '::processCall() {')
        end = text.index('\n};', begin) + len('\n};')
        replacement = '''void DarlingServer::Call::NAME::processCall() {
    int code = -ESRCH;
    FD peer;
    if (auto thread = _thread.lock()) {
        if (auto process = thread->process()) {
            try {
                auto channel = std::make_shared<Kqchan::CLASS>(process, ARGS);
                peer = FD(channel->setup());
                process->registerKqchan(channel);
                code = 0;
            } catch (const std::system_error& error) {
                code = -error.code().value();
                peer = FD();
            } catch (...) {
                code = -ESRCH;
                peer = FD();
            }
        }
    }
    // Construction and descriptor-capacity growth can fail. The peer belongs
    // to this RAII slot until pushDescriptor has completed its ownership copy.
    Message reply(sizeof(dserver_rpc_reply_WIRE_t), 0);
    reply.setAddress(_replyAddress);
    auto* body = reinterpret_cast<dserver_rpc_reply_WIRE_t*>(reply.data().data());
    body->header.number = dserver_callnum_WIRE;
    body->header.code = code;
    body->body.socket = peer ? 0 : -1;
    if (peer) {
        reply.pushDescriptor(peer.fd());
        peer.extract();
    }
    if (auto thread = _thread.lock()) thread->pushCallReply(shared_from_this(), std::move(reply));
    else Call::sendReply(std::move(reply));
};'''.replace('NAME', name).replace('CLASS', cls).replace('ARGS', args).replace('WIRE', wire)
        text = text[:begin] + replacement + text[end:]
    files['call.cpp'] = text
    return files
