#!/usr/bin/env python3
"""Own each opaque C task/thread context independently across exec generations.

Apply CPP after checked-IPC, prepared lifecycle and optional native test hooks.
Apply C after checked-IPC C transforms. No source/reference files are written.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('kernel generation context anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


def replace_function(text, begin, following, replacement):
    start = text.index(begin)
    end = text.index(following, start)
    return text[:start] + replacement + '\n\n' + text[end:]


def context_declarations(kind, handle):
    node = 'Task' if kind == 'Process' else 'Thread'
    return f'''
public:
    struct Kernel{node}Context {{
        std::shared_ptr<{kind}> owner;
        {handle}* handle = nullptr;
        explicit Kernel{node}Context(std::shared_ptr<{kind}> value): owner(std::move(value)) {{}}
    }};
    static {kind}* fromKernelContext(void*) noexcept;
    static bool isCurrentKernelContext(void*) noexcept;
    static {handle}* kernelHandleFromContext(void*) noexcept;
'''


PROCESS_METHODS = '''
DarlingServer::Process* DarlingServer::Process::fromKernelContext(void* raw) noexcept {
    return raw ? static_cast<KernelTaskContext*>(raw)->owner.get() : nullptr;
}
bool DarlingServer::Process::isCurrentKernelContext(void* raw) noexcept {
    if (!raw) return false;
    const auto* context = static_cast<KernelTaskContext*>(raw);
    return context->handle && context->owner->_dtapeTask.load() == context->handle;
}
dtape_task_t* DarlingServer::Process::kernelHandleFromContext(void* raw) noexcept {
    return raw ? static_cast<KernelTaskContext*>(raw)->handle : nullptr;
}
dtape_task_t* DarlingServer::Process::createOwnedTask(dtape_task_t* parent,
    Architecture architecture, int* status) {
    auto context = std::make_unique<KernelTaskContext>(shared_from_this());
    dtape_task_t* task = status
        ? dearust_dtape_task_prepare(parent, _nspid, context.get(),
              static_cast<dserver_rpc_architecture_t>(architecture), status)
        : dtape_task_create(parent, _nspid, context.get(),
              static_cast<dserver_rpc_architecture_t>(architecture));
    // A non-success checked result can still own a private partial task. Bind
    // its context before the caller inspects status and schedules its abort.
    if (task) { context->handle = task; context.release(); }
    return task;
}
void DarlingServer::Process::disposeTaskContext(void* raw, void* rawTask) noexcept {
    if (!raw) return; // The initial unadopted kernel task has no C++ owner yet.
    std::unique_ptr<KernelTaskContext> context(static_cast<KernelTaskContext*>(raw));
    auto self = context->owner;
    auto* task = static_cast<dtape_task_t*>(rawTask);
    if (!task || context->handle != task) std::terminate();
@PROCESS_DISPOSAL_HOOK@
    // A retired exec generation owns its C++ context independently, and must
    // never wait on the replacement's Process lock or alter its current owner.
    if (self->_dtapeTask.load() != task) return;
    {
        std::unique_lock lock(self->_rwlock);
        if (self->_dtapeTask.load() != task) return;
        self->_dispose();
    }
}
'''

THREAD_METHODS = '''
DarlingServer::Thread* DarlingServer::Thread::fromKernelContext(void* raw) noexcept {
    return raw ? static_cast<KernelThreadContext*>(raw)->owner.get() : nullptr;
}
bool DarlingServer::Thread::isCurrentKernelContext(void* raw) noexcept {
    if (!raw) return false;
    const auto* context = static_cast<KernelThreadContext*>(raw);
    return context->handle && context->owner->_dtapeThread.load() == context->handle;
}
dtape_thread_t* DarlingServer::Thread::kernelHandleFromContext(void* raw) noexcept {
    return raw ? static_cast<KernelThreadContext*>(raw)->handle : nullptr;
}
dtape_thread_t* DarlingServer::Thread::createOwnedThread(dtape_task_t* task, int* status) {
    auto context = std::make_unique<KernelThreadContext>(shared_from_this());
    dtape_thread_t* thread = status
        ? dearust_dtape_thread_prepare(task, _nstid, context.get(), status)
        : dtape_thread_create(task, _nstid, context.get());
    if (thread) { context->handle = thread; context.release(); }
    return thread;
}
void DarlingServer::Thread::disposeThreadContext(void* raw, void* rawThread) noexcept {
    if (!raw) return;
    std::unique_ptr<KernelThreadContext> context(static_cast<KernelThreadContext*>(raw));
    auto self = context->owner;
    auto* thread = static_cast<dtape_thread_t*>(rawThread);
    if (!thread || context->handle != thread) std::terminate();
@THREAD_DISPOSAL_HOOK@
    if (self->_dtapeThread.load() != thread) return;
    {
        std::unique_lock lock(self->_rwlock);
        if (self->_dtapeThread.load() != thread) return;
        self->_dispose();
    }
}
extern "C" void dearust_resource_thread_dispose(void* context, void* thread) noexcept {
    DarlingServer::Thread::disposeThreadContext(context, thread);
}
'''

PROCESS_PREPARE = '''void DarlingServer::Process::prepareKernelHandles() {
    if (_dtapeTask) { _selfReference = shared_from_this(); return; }
    const bool kernel = _pid < 0;
    if (!kernel) Thread::requireResourceCleanupReady();
    _selfReference = shared_from_this();
    try {
        auto parent = _parentProcess.lock();
        std::shared_lock<std::shared_mutex> parentLock;
        if (parent) parentLock = std::shared_lock(parent->_rwlock);
        if (parent && (parent->_dead || parent->_resourceCleanupQueued || !parent->_dtapeTask))
            throw std::system_error(ESRCH, std::generic_category());
        int status = 0;
        _dtapeTask = createOwnedTask(parent ? parent->_dtapeTask.load() : nullptr,
            _architecture, kernel ? nullptr : &status);
        if (status != 0) {
            _resourcePartialTask = _dtapeTask != nullptr;
            throw std::bad_alloc();
        }
        if (!_dtapeTask) throw std::bad_alloc();
        if (kernel) return; // All publication storage preceded kernel adoption.
        _dtapeForkWaitSemaphore = dtape_semaphore_create(_dtapeTask, 0);
        if (!_dtapeForkWaitSemaphore) throw std::bad_alloc();
    } catch (...) { abortKernelHandles(); throw; }
}
'''

THREAD_PREPARE = '''void DarlingServer::Thread::prepareKernelHandles() {
    if (_dtapeThread) { _selfReference = shared_from_this(); return; }
    const bool kernel = _tid < 0;
    // Bootstrap has no cleanup lane yet. Reserve its final allocating weak-map
    // slot first and retain that Process lock through C acquisition and commit.
    if (kernel) preparePublication();
    else requireResourceCleanupReady();
    _selfReference = shared_from_this();
    try {
        if (kernel) {
            _dtapeThread = createOwnedThread(_process->_dtapeTask);
            if (!_dtapeThread) throw std::bad_alloc();
            return;
        }
        std::shared_lock processLock(_process->_rwlock);
        if (_process->_dead || _process->_resourceCleanupQueued || !_process->_dtapeTask)
            throw std::system_error(ESRCH, std::generic_category());
        int status = 0;
        _dtapeThread = createOwnedThread(_process->_dtapeTask, &status);
        if (status != 0) {
            _resourcePartialThread = _dtapeThread != nullptr;
            throw std::bad_alloc();
        }
        if (!_dtapeThread) throw std::bad_alloc();
        _s2cPerformSempahore = dtape_semaphore_create(_process->_dtapeTask, 1);
        if (!_s2cPerformSempahore) throw std::bad_alloc();
        _s2cReplySempahore = dtape_semaphore_create(_process->_dtapeTask, 0);
        if (!_s2cReplySempahore) throw std::bad_alloc();
        _s2cInterruptEnterSemaphore = dtape_semaphore_create(_process->_dtapeTask, 0);
        if (!_s2cInterruptEnterSemaphore) throw std::bad_alloc();
        _s2cInterruptExitSemaphore = dtape_semaphore_create(_process->_dtapeTask, 0);
        if (!_s2cInterruptExitSemaphore) throw std::bad_alloc();
    } catch (...) { abortKernelHandles(); throw; }
}
'''


def terminal_dispose(text, kind, handle):
    signature = f'void DarlingServer::{kind}::_dispose() {{'
    begin = text.index(signature)
    end = text.index('\n};', begin) + len('\n};')
    body = text[begin:end]
    hook = f'    dearust_actual_cleanup_disposed({"false" if kind == "Process" else "true"});'
    moved_hook = hook if hook in body else ''
    replacement = signature + f'''
    // Only the exact-generation context disposer calls this with _rwlock held.
    {handle}.store(nullptr);
    _selfReference = nullptr;
}};
'''
    return text[:begin] + replacement + text[end:], moved_hook


def patch_kernel_context_cpp(files):
    files = dict(files)
    for name, kind, handle, field in (
        ('process', 'Process', 'dtape_task_t', '_dtapeTask'),
        ('thread', 'Thread', 'dtape_thread_t', '_dtapeThread'),
    ):
        header = '#include <atomic>\n#include <utility>\n' + files[name + '.hpp']
        if name == 'thread':
            header = once(header, 'dtape_thread_t* _dtapeThread = nullptr;',
                          'std::atomic<dtape_thread_t*> _dtapeThread{nullptr};')
        declarations = context_declarations(kind, handle)
        if name == 'process':
            declarations += '    dtape_task_t* createOwnedTask(dtape_task_t*, Architecture, int* status = nullptr);\n'
        else:
            declarations += ('    dtape_thread_t* createOwnedThread(dtape_task_t*, int* status = nullptr);\n'
                             '    static void disposeThreadContext(void*, void*) noexcept;\n')
        files[name + '.hpp'] = once(header, '\n\t};\n};\n', '\n' + declarations + '\n\t};\n};\n')

    process = files['process.cpp']
    # Bootstrap constructors must not publish raw this before shared ownership.
    begin = process.index('DarlingServer::Process::Process(KernelProcessConstructorTag')
    end = process.index('DarlingServer::Process::~Process()', begin)
    constructor = process[begin:end]
    create = '\t_dtapeTask = dtape_task_create(nullptr, _nspid, this, static_cast<dserver_rpc_architecture_t>(_architecture));'
    start = constructor.index(create)
    finish = constructor.index('\n};', start)
    constructor = constructor[:start] + '\t// Kernel adoption occurs after Registry storage and shared ownership.\n' + constructor[finish:]
    process = process[:begin] + constructor + process[end:]
    process_prepare = PROCESS_PREPARE
    old_prepare = process[process.index('void DarlingServer::Process::prepareKernelHandles() {'):
                          process.index('void DarlingServer::Process::abortKernelHandles()')]
    if 'dearust_actual_cleanup_acquired();' in old_prepare:
        process_prepare = once(process_prepare, '        if (kernel) return;',
                               '        if (!kernel) dearust_actual_cleanup_acquired();\n        if (kernel) return;')
        process_prepare = once(process_prepare, '        if (!_dtapeForkWaitSemaphore) throw std::bad_alloc();',
                               '        if (!_dtapeForkWaitSemaphore) throw std::bad_alloc();\n        dearust_actual_cleanup_acquired();')
    process = replace_function(process, 'void DarlingServer::Process::prepareKernelHandles() {',
                               'void DarlingServer::Process::abortKernelHandles()', process_prepare)
    process = once(process, '    if (!_dtapeTask && !_selfReference) return;', '''    if (!_dtapeTask && !_dtapeForkWaitSemaphore) {
        // No C generation acquired ownership. This also works before bootstrap
        // has a cleanup lane; Registry still owns the object during rollback.
        _selfReference = nullptr;
        return;
    }''')
    # Main replaces notifyCheckin with its full prepared exec transaction later.
    process = once(process, 'dtape_task_create(oldTask, _nspid, this, static_cast<dserver_rpc_architecture_t>(_architecture))',
                   'createOwnedTask(oldTask, _architecture)')
    process = once(process, 'auto oldThread = mainThread->_dtapeThread;',
                   'auto oldThread = mainThread->_dtapeThread.load();')
    process = once(process, 'dtape_thread_create(_dtapeTask, mainThread->_nstid, mainThread.get())',
                   'mainThread->createOwnedThread(_dtapeTask)')
    process, hook = terminal_dispose(process, 'Process', '_dtapeTask')
    begin = process.index('void DarlingServer::Process::disposeTaskContext(')
    end = process.index('extern "C" void dearust_resource_task_dispose(', begin)
    process = process[:begin] + process[end:]
    files['process.cpp'] = process + '\n' + PROCESS_METHODS.replace('@PROCESS_DISPOSAL_HOOK@', hook)

    thread = files['thread.cpp']
    begin = thread.index('DarlingServer::Thread::Thread(KernelThreadConstructorTag')
    end = thread.index('void DarlingServer::Thread::preparePublication()', begin)
    constructor = thread[begin:end]
    create = '\t_dtapeThread = dtape_thread_create(Process::kernelProcess()->_dtapeTask, _nstid, this);'
    start = constructor.index(create)
    finish = constructor.index('\n};', start)
    constructor = constructor[:start] + '\t// Kernel C ownership begins after shared ownership and publication staging.\n' + constructor[finish:]
    thread = thread[:begin] + constructor + thread[end:]
    thread_prepare = THREAD_PREPARE
    old_prepare = thread[thread.index('void DarlingServer::Thread::prepareKernelHandles() {'):
                        thread.index('void DarlingServer::Thread::abortKernelHandles()')]
    if 'dearust_actual_cleanup_acquired();' in old_prepare:
        for field in ('_dtapeThread', '_s2cPerformSempahore', '_s2cReplySempahore',
                      '_s2cInterruptEnterSemaphore', '_s2cInterruptExitSemaphore'):
            check = '        if (!' + field + ') throw std::bad_alloc();'
            # Only the guest branch has this indentation (kernel uses twelve).
            thread_prepare = once(thread_prepare, '\n' + check + '\n',
                                   '\n' + check + '\n        dearust_actual_cleanup_acquired();\n')
    thread = replace_function(thread, 'void DarlingServer::Thread::prepareKernelHandles() {',
                              'void DarlingServer::Thread::abortKernelHandles()', thread_prepare)
    thread = once(thread, '    if (!_dtapeThread && !_selfReference) return;', '''    if (!_dtapeThread && !_s2cPerformSempahore && !_s2cReplySempahore &&
        !_s2cInterruptEnterSemaphore && !_s2cInterruptExitSemaphore) {
        _selfReference = nullptr;
        return;
    }''')
    thread = once(thread, '''        thread = self->_dtapeThread;
        self->_dtapeThread = nullptr;''', '''        // The C generation pins this owner until its exact final disposer.
        // Keep the borrowed handle valid for older active calls until then.
        thread = self->_dtapeThread;''')
    thread = once(thread, 'Thread* thread = static_cast<Thread*>(dtape_thread_context(thread_handle));',
        '''void* context = dtape_thread_context(thread_handle);
    if (!Thread::isCurrentKernelContext(context)) return nullptr;
    Thread* thread = Thread::fromKernelContext(context);''')
    thread, hook = terminal_dispose(thread, 'Thread', '_dtapeThread')
    files['thread.cpp'] = thread + '\n' + THREAD_METHODS.replace('@THREAD_DISPOSAL_HOOK@', hook)
    files['server.cpp'] = patch_hooks(files['server.cpp'])
    return files


def patch_hooks(server):
    server = once(server, '#include <darlingserver/duct-tape.h>',
                  '#include <darlingserver/duct-tape.h>\nextern "C" dtape_task_t* dearust_thread_owning_task(dtape_thread_t*);')
    # Contexts are opaque in the C ABI. Only this typed adapter interprets them.
    server = server.replace('static_cast<DarlingServer::Process*>(task_context)',
                            'DarlingServer::Process::fromKernelContext(task_context)')
    server = server.replace('static_cast<DarlingServer::Thread*>(thread_context)',
                            'DarlingServer::Thread::fromKernelContext(thread_context)')
    server = once(server, 'DarlingServer::Process::fromKernelContext(task_context)->_dispose();',
        'DarlingServer::Process::disposeTaskContext(task_context, DarlingServer::Process::kernelHandleFromContext(task_context));')
    server = once(server, 'DarlingServer::Thread::fromKernelContext(thread_context)->_dispose();',
        'DarlingServer::Thread::disposeThreadContext(thread_context, DarlingServer::Thread::kernelHandleFromContext(thread_context));')
    # Current C execution follows the actual C thread's task, even while the
    # logical Process has committed a later exec generation.
    old = '''\t\tauto process = thread->process();
\t\tif (!process) {
\t\t\treturn NULL;
\t\t}
\t\treturn process->_dtapeTask;'''
    server = once(server, old, '\t\treturn dearust_thread_owning_task(thread->_dtapeThread);')
    # Impersonation compares typed owners, never a context node against raw this.
    server = once(server, 'if (thread_context == fakeThread.get()) {',
                  'if (DarlingServer::Thread::fromKernelContext(thread_context) == fakeThread.get()) {')
    server = once(server, 'return dtape_hook_thread_suspend(thread.get(), continuationCallback, continuationContext, unlockMe);',
        '''if (continuationCallback) thread->suspend([=]() { continuationCallback(continuationContext); }, unlockMe);
                    else thread->suspend(nullptr, unlockMe);
                    return;''')
    signature = '\tstatic void dtape_hook_thread_suspend('
    begin = server.index(signature)
    brace = server.index('{', begin)
    server = server[:brace + 1] + '''
        // A retired C thread cannot be the currently executing microthread.
        // Resuming a replacement here would corrupt its retained stack.
        if (!DarlingServer::Thread::isCurrentKernelContext(thread_context)) std::terminate();''' + server[brace + 1:]
    # Retired-generation notifications must never drive a replacement C thread.
    for name in ('resume', 'terminate', 'setup', 'set_pending_signal', 'set_pending_call_override'):
        signature = '\tstatic void dtape_hook_thread_' + name + '('
        begin = server.index(signature)
        brace = server.index('{', begin)
        server = server[:brace + 1] + '\n        if (!DarlingServer::Thread::isCurrentKernelContext(thread_context)) return;' + server[brace + 1:]
    begin = server.index('\tstatic dtape_thread_state_t dtape_hook_thread_get_state(')
    brace = server.index('{', begin)
    server = server[:brace + 1] + '\n        if (!DarlingServer::Thread::isCurrentKernelContext(thread_context)) return dtape_thread_state_dead;' + server[brace + 1:]
    begin = server.index('\tstatic int dtape_hook_thread_send_signal(')
    brace = server.index('{', begin)
    server = server[:brace + 1] + '\n        if (!DarlingServer::Thread::isCurrentKernelContext(thread_context)) return -ESRCH;' + server[brace + 1:]
    # An old task capability cannot read or mutate a later host exec image.
    errors = {'read_memory': 'false', 'write_memory': 'false', 'get_memory_region_info': 'false',
              'allocate_pages': '0', 'free_pages': '-1', 'map_file': '0', 'get_next_region': '0',
              'change_protection': 'false', 'sync_memory': 'false'}
    for name, error in errors.items():
        marker = 'dtape_hook_task_' + name + '('
        begin = server.index(marker)
        brace = server.index('{', begin)
        server = server[:brace + 1] + f'\n        if (!DarlingServer::Process::isCurrentKernelContext(task_context)) return {error};' + server[brace + 1:]
    begin = server.index('dtape_hook_task_get_memory_info(')
    brace = server.index('{', begin)
    server = server[:brace + 1] + '''
        if (!DarlingServer::Process::isCurrentKernelContext(task_context)) {
            memset(memory_info, 0, sizeof(*memory_info));
            return;
        }''' + server[brace + 1:]
    # Pin retained lookups while the owner still has its original C reference.
    # Once cleanup is queued, the borrowed pointer can be in final destruction
    # even though its exact context disposer has not cleared it yet.
    for kind, variable, field, retain in (
        ('Thread', 'thread', '_dtapeThread', 'dtape_thread_retain'),
        ('Process', 'process', '_dtapeTask', 'dtape_task_retain'),
    ):
        old = f'''\t\tif (retain) {{
\t\t\t{retain}({variable}->{field});
\t\t}}
\t\treturn {variable}->{field};'''
        new = f'''        std::shared_lock ownership({variable}->_rwlock);
        if ({variable}->_dead || {variable}->_resourceCleanupQueued) return nullptr;
        auto* handle = {variable}->{field}.load();
        if (retain && handle) {retain}(handle);
        return handle;'''
        if server.count(old) != 2:
            raise ValueError('expected numeric and eternal ' + kind + ' lookup hooks')
        server = server.replace(old, new)
    return server


def patch_kernel_context_c(files):
    files = dict(files)
    for key, name, noun in [('src/task.c', 'task', 'task'), ('src/thread.c', 'thread', 'thread')]:
        text = files[key]
        helper = 'dearust_resource_' + name + '_dispose'
        old = f'dtape_hooks->{name}_context_dispose({noun}->context);'
        # Both normal destruction and failed private preparation use identity.
        if old in text:
            text = text.replace(old, f'{helper}({noun}->context, {noun});')
        declaration = f'extern void {helper}(void*, void*);'
        if declaration not in text:
            text = declaration + '\n' + text
        if name == 'thread':
            text += '''
dtape_task_t* dearust_thread_owning_task(dtape_thread_t* thread) {
    return thread ? dtape_task_for_xnu_task(thread->xnu_thread.task) : NULL;
}
'''
        files[key] = text
    return files
