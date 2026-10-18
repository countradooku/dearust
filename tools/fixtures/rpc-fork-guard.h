/* Copied generated-RPC-only helpers. Not a replacement RPC protocol. */
extern unsigned long dearust_rpc_generation;
extern long dearust_rpc_syscall(long, long, long, long, long, long, int, unsigned long);
static unsigned long dearust_rpc_current_generation(void) {
    return __atomic_load_n(&dearust_rpc_generation, __ATOMIC_RELAXED);
}
static int dearust_rpc_abandoned(unsigned long generation) {
    return generation != dearust_rpc_current_generation();
}
/* Only called for a receive attempt whose ancillary storage was zeroed before
 * entering the syscall. It owns unconsumed SCM_RIGHTS from that attempt. */
static void dearust_rpc_discard_rights(struct linux_msghdr *message, unsigned long capacity) {
    unsigned long length = message->msg_controllen;
    if (length > capacity) length = capacity;
    unsigned char *cursor = message->msg_control;
    while (cursor && length >= sizeof(struct linux_cmsghdr)) {
        struct linux_cmsghdr *header = (struct linux_cmsghdr *)cursor;
        unsigned long size = header->cmsg_len;
        if (size < LINUX_CMSG_LEN(0) || size > length) break;
        if (header->cmsg_level == LINUX_SOL_SOCKET && header->cmsg_type == 1) {
            unsigned long count = (size - LINUX_CMSG_LEN(0)) / sizeof(int);
            int *fds = (int *)DSERVER_RPC_HOOKS_CMSG_DATA(header);
            for (unsigned long index = 0; index < count; index++) {
                if (fds[index] >= 0) LINUX_SYSCALL(__NR_close, fds[index]);
                fds[index] = -1;
            }
        }
        unsigned long step = (size + sizeof(long) - 1) & ~(sizeof(long) - 1);
        if (step > length) break;
        cursor += step; length -= step;
    }
    message->msg_controllen = 0;
}
/* Raw native masks retain unsupported RT bits. Block/restore failure cannot
 * authorize a partial transaction or continued execution with a poisoned mask. */
static inline void dearust_rpc_mask_failstop(void) {
    LINUX_SYSCALL(__NR_exit_group, 127);
    __builtin_trap();
}
static inline void dearust_rpc_mask_begin(linux_sigset_t *previous) {
    linux_sigset_t standard = 0x7fffffff;
    *previous = 0;
    if (LINUX_SYSCALL(__NR_rt_sigprocmask, 0, &standard, previous, sizeof(*previous)) < 0)
        dearust_rpc_mask_failstop();
}
static inline void dearust_rpc_mask_end(linux_sigset_t *previous) {
    if (LINUX_SYSCALL(__NR_rt_sigprocmask, 2, previous, NULL, sizeof(*previous)) < 0)
        dearust_rpc_mask_failstop();
}
