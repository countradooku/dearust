"""Compose durable Process death with prepared cleanup and reserved notifications.

The borrowed task pointer remains valid until the final C context disposal:
active Threads own C task references while finishing already accepted calls.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('resource death lifecycle anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


NOTIFY_PROCESS_DEAD = '''void DarlingServer::Process::notifyDead() {
    decltype(_threads) threads;
    decltype(_kqchannels) channels;
    {
        std::unique_lock lock(_rwlock);
        if (_dead) return;
        _selfReference = shared_from_this();
        _dead = true;
        threads.swap(_threads);
        channels.swap(_kqchannels);
    }
    // Nothing below requires new C++ allocation. In particular, channel
    // destructors may unregister self-subscriptions: never run them with our
    // Process lock held. Resource shutdown also rejects late CPU rearming.
    resourceDying();
    channels.clear();
    notifyPreparedExitListeners();
    dtape_task_dying(_dtapeTask);
    {
        std::unique_lock lock(_rwlock);
        _preparedTaskDying = true;
    }
    for (auto& item: threads)
        if (auto thread = item.second.lock()) thread->notifyDead();
    abortKernelHandles();
    processRegistry().unregisterEntry(shared_from_this());
};
'''


def patch_death_lifecycle(files):
    files = dict(files)
    files['process.hpp'] = '#include <atomic>\n' + files['process.hpp']
    files['process.hpp'] = once(files['process.hpp'],
        'dtape_task_t* _dtapeTask = nullptr;', 'std::atomic<dtape_task_t*> _dtapeTask{nullptr};')
    files['process.hpp'] = once(files['process.hpp'], '\t\tvoid notifyDead();',
        '\t\tvoid notifyDead();\n        static void disposeTaskContext(void*, void*) noexcept;')
    files['process.hpp'] = once(files['process.hpp'],
        '        bool _resourceCleanupQueued = false;',
        '        bool _resourceCleanupQueued = false;\n        bool _preparedTaskDying = false;')
    text = files['process.cpp']
    text = once(text, 'auto oldTask = _dtapeTask;', 'auto oldTask = _dtapeTask.load();')
    text = text.replace('parent ? parent->_dtapeTask : nullptr', 'parent ? parent->_dtapeTask.load() : nullptr')
    begin = text.index('void DarlingServer::Process::notifyDead() {')
    end = text.index('void DarlingServer::Process::_dispose()', begin)
    text = text[:begin] + NOTIFY_PROCESS_DEAD + '\n' + text[end:]
    begin = text.index('void DarlingServer::Process::_dispose() {')
    end = text.index('\n};', begin)
    dispose = text[begin:end]
    dispose = once(dispose, '\t_selfReference = nullptr;', '''    // Active Threads/knote references keep this C task valid after its owner
    // reference is dropped. Preserve the borrowed pointer until final disposal.
    auto self = _selfReference;
    {
        std::unique_lock lock(_rwlock);
        _dtapeTask = nullptr;
        _selfReference = nullptr;
    }''')
    text = text[:begin] + dispose + text[end:]
    text = once(text, 'if (parent && (parent->_dead || !parent->_dtapeTask))',
        'if (parent && (parent->_dead || parent->_resourceCleanupQueued || !parent->_dtapeTask))')
    text = once(text, '''        task = self->_dtapeTask;
        self->_dtapeTask = nullptr;''', '''        // Dropping the owner's C reference does not invalidate a task still
        // pinned by active Threads. Preserve the borrowed pointer for them.
        task = self->_dtapeTask;''')
    text = once(text, '    dtape_task_t* task;', '    dtape_task_t* task;\n    bool alreadyDying;')
    text = once(text, '        task = self->_dtapeTask;', '        task = self->_dtapeTask;\n        alreadyDying = self->_preparedTaskDying;')
    text = once(text, '        dtape_task_dying(task);',
                   '        if (!alreadyDying) dtape_task_dying(task);')
    text += '''
void DarlingServer::Process::disposeTaskContext(void* context, void* rawTask) noexcept {
    auto* process = static_cast<Process*>(context);
    auto* task = static_cast<dtape_task_t*>(rawTask);
    // exec can retire a prior task while the same Process already represents
    // its replacement and still holds its lock. That callback must never clear
    // the replacement's handle/self-owner or recursively acquire its lock.
    if (!process->_dtapeTask.compare_exchange_strong(task, nullptr)) return;
    process->_dispose();
}
extern "C" void dearust_resource_task_dispose(void* context, void* task) noexcept {
    DarlingServer::Process::disposeTaskContext(context, task);
}
'''
    files['process.cpp'] = text
    files['thread.cpp'] = once(files['thread.cpp'],
        'if (_process->_dead || !_process->_dtapeTask)',
        'if (_process->_dead || _process->_resourceCleanupQueued || !_process->_dtapeTask)')
    return files


def patch_task_dispose_identity(text):
    old = 'dtape_hooks->task_context_dispose(task->context);'
    return ('extern void dearust_resource_task_dispose(void*, void*);\n' +
            once(text, old, 'dearust_resource_task_dispose(task->context, task);'))
