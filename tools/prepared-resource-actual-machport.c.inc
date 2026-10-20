// Test-only ordinary native lifecycle helpers, compiled with real XNU types.
// No sockets/RPCs/guest messages enter the Mach port. The owned receive right
// supplies a real mqueue/knote to exercise production callback and detach paths.
#include <mach/mach_port.h>
#include <kern/task.h>
#include <ipc/ipc_space.h>
#include <ipc/ipc_port.h>
#include <kern/ipc_mig.h>
extern void dearust_actual_mach_snapshot(void* handle);
extern void dearust_actual_mach_detach_begin(void* handle);

uint32_t dearust_actual_mach_allocate_port(void) {
    mach_port_name_t name = MACH_PORT_NULL;
    if (mach_port_allocate(current_task()->itk_space, MACH_PORT_RIGHT_RECEIVE, &name) != KERN_SUCCESS)
        return MACH_PORT_NULL;
    return name;
}

uint32_t dearust_actual_mach_allocate_portset(void) {
    mach_port_name_t name = MACH_PORT_NULL;
    if (mach_port_allocate(current_task()->itk_space, MACH_PORT_RIGHT_PORT_SET, &name) != KERN_SUCCESS)
        return MACH_PORT_NULL;
    return name;
}

int dearust_actual_mach_join_portset(uint32_t port, uint32_t set) {
    return mach_port_move_member(current_task()->itk_space, port, set);
}

int dearust_actual_mach_send(uint32_t name) {
    ipc_port_t port;
    if (ipc_port_translate_receive(current_task()->itk_space, name, &port) != KERN_SUCCESS) return 1;
    ipc_port_t send = ipc_port_make_send_locked(port);
    ip_unlock(port);
    mach_msg_header_t message = {0};
    message.msgh_bits = MACH_MSGH_BITS(MACH_MSG_TYPE_MOVE_SEND, 0);
    message.msgh_size = sizeof(message);
    message.msgh_remote_port = send;
    message.msgh_id = 123;
    return mach_msg_send_from_kernel_proper(&message, sizeof(message));
}

int dearust_actual_mach_destroy_port(uint32_t name) {
    return mach_port_destroy(current_task()->itk_space, name);
}

unsigned dearust_actual_mach_task_references(void) {
    return os_ref_get_count(&current_task()->ref_count);
}

int dearust_actual_mach_invalid_attach(void) {
    dtape_task_t* task = dtape_task_for_xnu_task(current_task());
    const unsigned before = os_ref_get_count(&task->xnu_task.ref_count);
    dtape_kqchan_mach_port_t* channel = dtape_kqchan_mach_port_create(
        task, MACH_PORT_NULL, 0, 0, 0, NULL, NULL);
    if (channel) { dtape_kqchan_mach_port_destroy(channel); return 0; }
    return os_ref_get_count(&task->xnu_task.ref_count) == before;
}

void dearust_actual_mach_post(void* raw) {
    dtape_kqchan_mach_port_t* channel = raw;
    ipc_mqueue_t queue = channel->knote.kn_mqueue;
    // Same lock contract as ipc_mqueue's ordinary port-message knote path.
    imq_lock(queue);
    knote_post(&channel->knote, 0);
    imq_unlock(queue);
}
