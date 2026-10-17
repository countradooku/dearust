/*
 * Copyright (c) 2000 Apple Computer, Inc. All rights reserved.
 *
 * @APPLE_OSREFERENCE_LICENSE_HEADER_START@
 *
 * This file contains Original Code and/or Modifications of Original Code
 * as defined in and that are subject to the Apple Public Source License
 * Version 2.0 (the 'License'). You may not use this file except in
 * compliance with the License. The rights granted to you under the License
 * may not be used to create, or enable the creation or redistribution of,
 * unlawful or unlicensed copies of an Apple operating system, or to
 * circumvent, violate, or enable the circumvention or violation of, any
 * terms of an Apple operating system software license agreement.
 *
 * Please obtain a copy of the License at
 * http://www.opensource.apple.com/apsl/ and read it before using this file.
 *
 * The Original Code and all software distributed under the License are
 * distributed on an 'AS IS' basis, WITHOUT WARRANTY OF ANY KIND, EITHER
 * EXPRESS OR IMPLIED, AND APPLE HEREBY DISCLAIMS ALL SUCH WARRANTIES,
 * INCLUDING WITHOUT LIMITATION, ANY WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE, QUIET ENJOYMENT OR NON-INFRINGEMENT.
 * Please see the License for the specific language governing rights and
 * limitations under the License.
 *
 * @APPLE_OSREFERENCE_LICENSE_HEADER_END@
 */
/* Port conversion and no-senders pattern adapted from XNU ipc_sync.c. */
/* XNU structure/macros and scheduler ABI bridge. All lockset state is in Rust. */
#include <mach/mach_types.h>
#include <kern/sync_lock.h>
#include <kern/sched_prim.h>
#include <kern/ipc_kobject.h>
#include <kern/thread.h>
#include <kern/task.h>
#include <ipc/ipc_port.h>

uintptr_t darling_lockset_current_thread(void) { return (uintptr_t)current_thread(); }
int darling_lockset_priority(void) {
    thread_t thread = current_thread();
    thread_lock(thread);
    int priority = thread->sched_pri;
    thread_unlock(thread);
    return priority;
}
bool darling_lockset_task_lock_active(uintptr_t value) {
    task_t task = (task_t)value;
    task_lock(task);
    if (!task->active) { task_unlock(task); return false; }
    return true;
}
void darling_lockset_task_unlock(uintptr_t task) { task_unlock((task_t)task); }
int darling_lockset_prepare_wait(uintptr_t event) { return assert_wait((event_t)event, THREAD_INTERRUPTIBLE); }
int darling_lockset_block(void) { return thread_block(THREAD_CONTINUE_NULL); }
void darling_lockset_wake(uintptr_t event) { thread_wakeup((event_t)event); }
bool darling_lockset_make_port(uintptr_t id, uintptr_t *port) {
    ipc_port_t native_port = (ipc_port_t)*port;
    bool donated = ipc_kobject_make_send_lazy_alloc_port(&native_port,
        (ipc_kobject_t)id, IKOT_LOCK_SET, IPC_KOBJECT_ALLOC_NONE, false, 0);
    *port = (uintptr_t)native_port;
    return donated;
}
void darling_lockset_drop_port(uintptr_t port) { ipc_port_dealloc_kernel((ipc_port_t)port); }
lock_set_t convert_port_to_lock_set(ipc_port_t port) {
    if (!IP_VALID(port) || ip_kotype(port) != IKOT_LOCK_SET) return LOCK_SET_NULL;
    require_ip_active(port);
    lock_set_t set = (lock_set_t)ip_get_kobject(port);
    lock_set_reference(set);
    return set;
}
void darling_lockset_notify(mach_msg_header_t *message) {
    ipc_port_t port = message->msgh_remote_port;
    require_ip_active(port);
    lock_set_dereference((lock_set_t)ip_get_kobject(port));
}
