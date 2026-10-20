#!/usr/bin/env python3
"""Follow-on checked dtape acquisition with explicit unpublished ownership.

Apply after patch_prepared_resource_inner_dtape. Legacy bootstrap C APIs remain
unchanged; ordinary prepared C++ registration uses the new checked APIs. A failed
checked preparation returns an owned partial handle, released only by its private
stage-aware kernel-lane abort API, never by normal task/thread destruction.
"""


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('checked IPC anchor not unique: ' + old[:100])
    return text.replace(old, new, 1)


def function(text, signature, ending='\n}\n'):
    start = text.index(signature)
    return text[start:text.index(ending, start) + len(ending)]


PORT_TRY = '''
/* Private checked allocation: even an error returns ownership of an acquired
 * port through output. The caller's explicit partial-state abort destroys it. */
kern_return_t dearust_ipc_kobject_try_port(ipc_kobject_t object,
    ipc_kobject_type_t type, ipc_label_t label, boolean_t labeled,
    ipc_kobject_alloc_options_t options, ipc_port_t* output) {
    ipc_port_t port = ipc_port_alloc_kernel();
    *output = port;
    if (port == IP_NULL) return KERN_RESOURCE_SHORTAGE;
    if (labeled) {
        ipc_kobject_label_t storage = zalloc(ipc_kobject_label_zone);
        if (storage == NULL) return KERN_RESOURCE_SHORTAGE;
        storage->ikol_label = label;
        port->ip_kolabel = storage;
        port->ip_object.io_bits |= IO_BITS_KOLABEL;
    }
    ipc_kobject_init_port(port, object, type, options);
    return KERN_SUCCESS;
}
'''

IPC_DECLARATION = '''
extern kern_return_t dearust_ipc_kobject_try_port(ipc_kobject_t,
    ipc_kobject_type_t, ipc_label_t, boolean_t, ipc_kobject_alloc_options_t,
    ipc_port_t*);
'''

IPC_ABORT = '''
/* These abort routines accept only failed private initialization before send
 * rights/inheritance/exception actions were installed. All slots were zeroed;
 * each acquired port is stored immediately, including on a labeled-port error. */
static void dearust_ipc_abort_unpublished_ports(ipc_port_t control,
    ipc_port_t self, ipc_port_t name) {
    if (control != IP_NULL) {
        ip_lock(control);
        control->ip_alt_port = IP_NULL;
        ipc_kobject_set_atomically(control, IKO_NULL, IKOT_NONE);
        ip_unlock(control);
    }
    if (self != IP_NULL && self != control) {
        ipc_kobject_set(self, IKO_NULL, IKOT_NONE);
        ipc_port_dealloc_kernel(self);
    }
    if (control != IP_NULL) ipc_port_dealloc_kernel(control);
    if (name != IP_NULL) {
        ipc_kobject_set(name, IKO_NULL, IKOT_NONE);
        ipc_port_dealloc_kernel(name);
    }
}
void dearust_ipc_task_abort_unpublished(task_t task) {
    assert(task->itk_settable_self == IP_NULL);
    dearust_ipc_abort_unpublished_ports(task->itk_task_ports[TASK_FLAVOR_CONTROL],
        task->itk_self, task->itk_task_ports[TASK_FLAVOR_NAME]);
    if (task->itk_space != IS_NULL) {
        ipc_space_terminate(task->itk_space);
        is_release(task->itk_space);
    }
    itk_lock_destroy(task);
}
void dearust_ipc_thread_abort_unpublished(thread_t thread) {
    assert(thread->ith_settable_self == IP_NULL);
    dearust_ipc_abort_unpublished_ports(thread->ith_thread_ports[THREAD_FLAVOR_CONTROL],
        thread->ith_self, IP_NULL);
}
'''


def patch_ipc_tt(text):
    task = function(text, 'void\nipc_task_init(')
    task = once(task, 'void\nipc_task_init(', 'kern_return_t\ndearust_ipc_task_try_init(')
    task = once(task, '\tkr = ipc_space_create(', '\titk_lock_init(task);\n\tkr = ipc_space_create(')
    task = once(task, '\tif (kr != KERN_SUCCESS) {\n\t\tpanic("ipc_task_init");\n\t}',
                '\tif (kr != KERN_SUCCESS) {\n\t\treturn kr;\n\t}')
    task = once(task, '\tspace->is_task = task;', '\tspace->is_task = task;\n\ttask->itk_space = space;')
    task = once(task, '''\t\tpport = ipc_kobject_alloc_port(IKO_NULL, IKOT_NONE, options);

\t\tkport = ipc_kobject_alloc_labeled_port(IKO_NULL, IKOT_TASK_CONTROL,
\t\t    IPC_LABEL_SUBST_TASK, IPC_KOBJECT_ALLOC_NONE);''', '''        kr = dearust_ipc_kobject_try_port(IKO_NULL, IKOT_NONE, IPC_LABEL_NONE,
            FALSE, options, &pport);
        task->itk_self = pport;
        if (kr != KERN_SUCCESS) return kr;
        kr = dearust_ipc_kobject_try_port(IKO_NULL, IKOT_TASK_CONTROL,
            IPC_LABEL_SUBST_TASK, TRUE, IPC_KOBJECT_ALLOC_NONE, &kport);
        task->itk_task_ports[TASK_FLAVOR_CONTROL] = kport;
        if (kr != KERN_SUCCESS) return kr;''')
    task = once(task, '''\t\tkport = ipc_kobject_alloc_port(IKO_NULL, IKOT_TASK_CONTROL,
\t\t    IPC_KOBJECT_ALLOC_NONE);

\t\tpport = kport;''', '''        kr = dearust_ipc_kobject_try_port(IKO_NULL, IKOT_TASK_CONTROL,
            IPC_LABEL_NONE, FALSE, IPC_KOBJECT_ALLOC_NONE, &kport);
        task->itk_task_ports[TASK_FLAVOR_CONTROL] = kport;
        task->itk_self = kport;
        if (kr != KERN_SUCCESS) return kr;
        pport = kport;''')
    task = once(task, '\tnport = ipc_port_alloc_kernel();',
                '\tnport = ipc_port_alloc_kernel();\n\ttask->itk_task_ports[TASK_FLAVOR_NAME] = nport;')
    if task.count('\t\tpanic("ipc_task_init");') != 2:
        raise ValueError('expected remaining task port panic sites')
    task = task.replace('\t\tpanic("ipc_task_init");', '\t\treturn KERN_RESOURCE_SHORTAGE;')
    # Move, rather than duplicate, initialization of the primitive IPC task lock.
    task = once(task, '\titk_lock_init(task);\n\ttask->itk_task_ports', '\ttask->itk_task_ports')
    task = task[:-len('\n}\n')] + '\n\treturn KERN_SUCCESS;\n}\n'
    thread = function(text, 'void\nipc_thread_init(')
    thread = once(thread, 'void\nipc_thread_init(', 'kern_return_t\ndearust_ipc_thread_try_init(')
    thread = once(thread, '\tipc_port_t      kport;', '\tkern_return_t kr;\n\tipc_port_t      kport;')
    thread = once(thread, '''\t\tpport = ipc_kobject_alloc_port((ipc_kobject_t)thread,
\t\t    IKOT_THREAD_CONTROL, alloc_options);

\t\tkport = ipc_kobject_alloc_labeled_port((ipc_kobject_t)thread,
\t\t    IKOT_THREAD_CONTROL, IPC_LABEL_SUBST_THREAD, IPC_KOBJECT_ALLOC_NONE);''', '''        kr = dearust_ipc_kobject_try_port((ipc_kobject_t)thread, IKOT_THREAD_CONTROL,
            IPC_LABEL_NONE, FALSE, alloc_options, &pport);
        thread->ith_self = pport;
        if (kr != KERN_SUCCESS) return kr;
        kr = dearust_ipc_kobject_try_port((ipc_kobject_t)thread, IKOT_THREAD_CONTROL,
            IPC_LABEL_SUBST_THREAD, TRUE, IPC_KOBJECT_ALLOC_NONE, &kport);
        thread->ith_thread_ports[THREAD_FLAVOR_CONTROL] = kport;
        if (kr != KERN_SUCCESS) return kr;''')
    thread = once(thread, '''\t\tkport = ipc_kobject_alloc_port((ipc_kobject_t)thread,
\t\t    IKOT_THREAD_CONTROL, IPC_KOBJECT_ALLOC_NONE);

\t\tpport = kport;''', '''        kr = dearust_ipc_kobject_try_port((ipc_kobject_t)thread, IKOT_THREAD_CONTROL,
            IPC_LABEL_NONE, FALSE, IPC_KOBJECT_ALLOC_NONE, &kport);
        thread->ith_thread_ports[THREAD_FLAVOR_CONTROL] = kport;
        thread->ith_self = kport;
        if (kr != KERN_SUCCESS) return kr;
        pport = kport;''')
    thread = thread[:-len('\n}\n')] + '\n\treturn KERN_SUCCESS;\n}\n'
    return text + '\n' + IPC_DECLARATION + task + '\n' + thread + '\n' + IPC_ABORT


def patch_psynch(text):
    return text + '''
/* Checked dtape-only equivalent. The original XNU no-fail API is unchanged. */
int dearust_pth_proc_try_hashinit(proc_t process) {
    void* pointer = hashinit(PTH_HASHSIZE, M_PCB, &pthhash);
    if (pointer == NULL) return 0;
    pthread_kern->proc_set_pthhash(process, pointer);
    return 1;
}
'''


def patch_checked_task(text):
    original = function(text, 'dtape_task_t* dtape_task_create(', '\n};')
    body = original[original.index('\tdtape_task_t* task = malloc('):]
    body = once(body, 'malloc(sizeof(dtape_task_t))', 'malloc(sizeof(struct dearust_checked_task))')
    body = once(body, '\ttask->context = context;', '''    struct dearust_checked_task* state = (void*)task;
    state->ipc_started = state->ipc_complete = false;
    task->p_pthhash = NULL;
\ttask->context = context;''')
    body = once(body, '\tipc_task_init(&task->xnu_task, parent_task ? &parent_task->xnu_task : NULL);', '''    state->ipc_started = true;
    if (dearust_ipc_task_try_init(&task->xnu_task,
            parent_task ? &parent_task->xnu_task : NULL) != KERN_SUCCESS) return task;
    state->ipc_complete = true;''')
    body = once(body, '\t\ttask_importance_init_from_parent(&task->xnu_task, &parent_task->xnu_task);',
                '\t\tif (dearust_task_importance_try_inherit(&task->xnu_task, &parent_task->xnu_task) != KERN_SUCCESS) return task;')
    body = once(body, '\t\tipc_importance_task_t imp = ipc_importance_for_task(&task->xnu_task, false);',
                '\t\tipc_importance_task_t imp = ipc_importance_for_task(&task->xnu_task, false);\n'
                '\t\tif (imp == IIT_NULL) return task;')
    body = once(body, '\tipc_task_enable(&task->xnu_task);\n\n\tdtape_psynch_task_init(task);',
                '\tif (!dearust_pth_proc_try_hashinit((void*)task)) return task;\n\tipc_task_enable(&task->xnu_task);')
    start = body.index('\tif (parent_task == NULL && nsid == 0) {')
    end = body.index('\n\treturn task;', start)
    body = body[:start] + '\t*status = KERN_SUCCESS;\n' + body[end:]
    inherit = function(text, 'void\ntask_importance_init_from_parent(')
    inherit = once(inherit, 'void\ntask_importance_init_from_parent(',
                   'static kern_return_t\ndearust_task_importance_try_inherit(')
    inherit = once(inherit, '\t\treturn;', '\t\treturn KERN_SUCCESS;')
    inherit = inherit.replace('assert(IIT_NULL != new_task_imp);',
                              'if (IIT_NULL == new_task_imp) return KERN_RESOURCE_SHORTAGE;')
    inherit = inherit[:-len('\n}\n')] + '\n\treturn KERN_SUCCESS;\n}\n'
    prefix = '''
#include <ipc/ipc_space.h>
extern kern_return_t dearust_ipc_task_try_init(task_t, task_t);
extern void dearust_ipc_task_abort_unpublished(task_t);
extern int dearust_pth_proc_try_hashinit(void*);
struct dearust_checked_task {
    dtape_task_t task;
    bool ipc_started;
    bool ipc_complete;
};
'''
    prepare = '''dtape_task_t* dearust_dtape_task_prepare(dtape_task_t* parent_task,
    uint32_t nsid, void* context, dserver_rpc_architecture_t architecture, int* status) {
    *status = KERN_RESOURCE_SHORTAGE;
''' + body
    abort = '''
/* Only the private prepare API's non-success handle reaches this function.
 * IPC was never enabled, no thread was attached, and every acquired field is
 * explicitly owned by the sidecar's completed initialization stages. */
void dearust_dtape_task_abort_partial(dtape_task_t* task) {
    struct dearust_checked_task* state = (void*)task;
    task->xnu_task.active = false;
    if (task->xnu_task.task_imp_base != IIT_NULL)
        ipc_importance_disconnect_task(&task->xnu_task);
    assert(os_ref_get_count(&task->xnu_task.ref_count) == 1);
    if (task->p_pthhash != NULL) dtape_psynch_task_destroy(task);
    if (state->ipc_complete) {
        ipc_task_disable(&task->xnu_task);
        ipc_space_terminate(task->xnu_task.itk_space);
        ipc_task_terminate(&task->xnu_task);
        is_release(task->xnu_task.itk_space);
    } else if (state->ipc_started) {
        dearust_ipc_task_abort_unpublished(&task->xnu_task);
    }
    dtape_vm_map_destroy(task->xnu_task.map);
    lck_mtx_destroy(&task->xnu_task.lock, LCK_GRP_NULL);
    assert(os_ref_release(&task->xnu_task.ref_count) == 0);
    dtape_hooks->task_context_dispose(task->context);
    free(task);
}
'''
    if 'dearust_resource_task_dispose(task->context, task);' in text:
        abort = once(abort, 'dtape_hooks->task_context_dispose(task->context);',
                     'dearust_resource_task_dispose(task->context, task);')
    return text + '\n' + prefix + inherit + '\n' + prepare + '\n' + abort


def patch_checked_thread(text):
    original = function(text, 'dtape_thread_t* dtape_thread_create(', '\n};')
    prepare = once(original, 'dtape_thread_t* dtape_thread_create(dtape_task_t* task, uint64_t nsid, void* context) {',
        'dtape_thread_t* dearust_dtape_thread_prepare(dtape_task_t* task, uint64_t nsid, void* context, int* status) {\n'
        '    *status = KERN_RESOURCE_SHORTAGE;')
    prepare = once(prepare, '\tipc_thread_init(&thread->xnu_thread, IPC_THREAD_INIT_NONE);', '''    // Retain the task before a partial IPC state can outlive this call.
    task_reference_internal(&task->xnu_task);
    if (dearust_ipc_thread_try_init(&thread->xnu_thread, IPC_THREAD_INIT_NONE) != KERN_SUCCESS)
        return thread;''')
    prepare = once(prepare, '\ttask_reference_internal(&task->xnu_task);', '')
    prepare = once(prepare, '\treturn thread;', '\t*status = KERN_SUCCESS;\n\treturn thread;')
    prefix = '''
extern kern_return_t dearust_ipc_thread_try_init(thread_t, ipc_thread_init_options_t);
extern void dearust_ipc_thread_abort_unpublished(thread_t);
'''
    abort = '''
void dearust_dtape_thread_abort_partial(dtape_thread_t* thread) {
    // Failed private IPC preparation precedes task-list ownership, timers,
    // psynch state, and all semaphores. It owns exactly one task reference.
    assert(!thread->xnu_thread.active);
    assert(os_ref_get_count(&thread->xnu_thread.ref_count) == 1);
    dearust_ipc_thread_abort_unpublished(&thread->xnu_thread);
    turnstile_deallocate(thread->xnu_thread.turnstile);
    lck_mtx_destroy(&thread->xnu_thread.mutex, LCK_GRP_NULL);
    task_deallocate(thread->xnu_thread.task);
    assert(os_ref_release(&thread->xnu_thread.ref_count) == 0);
    dtape_hooks->thread_context_dispose(thread->context);
    free(thread);
}
'''
    if 'dearust_resource_thread_dispose(thread->context, thread);' in text:
        abort = once(abort, 'dtape_hooks->thread_context_dispose(thread->context);',
                     'dearust_resource_thread_dispose(thread->context, thread);')
    return text + '\n' + prefix + prepare + '\n' + abort


def patch_checked_c(files):
    files = dict(files)
    files['src/task.c'] = patch_checked_task(files['src/task.c'])
    files['src/thread.c'] = patch_checked_thread(files['src/thread.c'])
    files['xnu/osfmk/kern/ipc_tt.c'] = patch_ipc_tt(files['xnu/osfmk/kern/ipc_tt.c'])
    files['xnu/osfmk/kern/ipc_kobject.c'] += '\n' + PORT_TRY
    files['pthread/kern_synch.c'] = patch_psynch(files['pthread/kern_synch.c'])
    return files


def patch_checked_cpp(files):
    files = dict(files)
    for name, kind in [('process', 'Task'), ('thread', 'Thread')]:
        files[name + '.hpp'] = once(files[name + '.hpp'], '        bool _resourceCleanupQueued = false;',
            '        bool _resourceCleanupQueued = false;\n        bool _resourcePartial' + kind + ' = false;')
    p = files['process.cpp']
    anchor = 'void DarlingServer::Process::prepareKernelHandles() {'
    p = once(p, anchor, '''extern "C" dtape_task_t* dearust_dtape_task_prepare(dtape_task_t*, uint32_t, void*, dserver_rpc_architecture_t, int*);
extern "C" void dearust_dtape_task_abort_partial(dtape_task_t*);
''' + anchor)
    parent_task = 'parent ? parent->_dtapeTask : nullptr'
    if 'parent ? parent->_dtapeTask.load() : nullptr' in p:
        parent_task = 'parent ? parent->_dtapeTask.load() : nullptr'
    p = once(p, '        _dtapeTask = dtape_task_create(' + parent_task + ''',
            _nspid, this, static_cast<dserver_rpc_architecture_t>(_architecture));''', '''        int prepareStatus = 0;
        _dtapeTask = dearust_dtape_task_prepare(''' + parent_task + ''',
            _nspid, this, static_cast<dserver_rpc_architecture_t>(_architecture), &prepareStatus);
        if (prepareStatus != 0) {
            _resourcePartialTask = _dtapeTask != nullptr;
            throw std::bad_alloc();
        }''')
    dying = '        dtape_task_dying(task);'
    if '        if (!alreadyDying) dtape_task_dying(task);' in p:
        dying = '        if (!alreadyDying) dtape_task_dying(task);'
    p = once(p, dying + '\n        dtape_task_release(task);',
        '''        if (self->_resourcePartialTask) dearust_dtape_task_abort_partial(task);
        else {
    ''' + dying + '''
            dtape_task_release(task);
        }''')
    files['process.cpp'] = p
    t = files['thread.cpp']
    anchor = 'void DarlingServer::Thread::prepareKernelHandles() {'
    t = once(t, anchor, '''extern "C" dtape_thread_t* dearust_dtape_thread_prepare(dtape_task_t*, uint64_t, void*, int*);
extern "C" void dearust_dtape_thread_abort_partial(dtape_thread_t*);
''' + anchor)
    t = once(t, '        _dtapeThread = dtape_thread_create(_process->_dtapeTask, _nstid, this);',
        '''        int prepareStatus = 0;
        _dtapeThread = dearust_dtape_thread_prepare(_process->_dtapeTask, _nstid, this, &prepareStatus);
        if (prepareStatus != 0) {
            _resourcePartialThread = _dtapeThread != nullptr;
            throw std::bad_alloc();
        }''')
    t = once(t, '        dtape_thread_dying(thread);\n        dtape_thread_release(thread);',
        '''        if (self->_resourcePartialThread) dearust_dtape_thread_abort_partial(thread);
        else {
            dtape_thread_dying(thread);
            dtape_thread_release(thread);
        }''')
    files['thread.cpp'] = t
    return files
