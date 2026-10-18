/* Transaction-bound Linux message syscall. No stack allocation or unwinding.
 * Signal return in a forked child may redirect any PC in this leaf to cancel.
 * Return -4096 is private to the copied generated-RPC wrapper. */
#ifdef __APPLE__
#define C(name) _##name
#else
#define C(name) name
#endif
.text
.p2align 4
.globl C(dearust_rpc_syscall)
.globl C(dearust_rpc_leaf_begin)
.globl C(dearust_rpc_leaf_end)
.globl C(dearust_rpc_leaf_cancel)
#ifdef __APPLE__
.private_extern C(dearust_rpc_syscall)
.private_extern C(dearust_rpc_leaf_begin)
.private_extern C(dearust_rpc_leaf_end)
.private_extern C(dearust_rpc_leaf_cancel)
.alt_entry C(dearust_rpc_leaf_begin)
.alt_entry C(dearust_rpc_leaf_end)
.alt_entry C(dearust_rpc_leaf_cancel)
#endif
C(dearust_rpc_syscall):
C(dearust_rpc_leaf_begin):
    movq 16(%rsp), %rax
    cmpq C(dearust_rpc_generation)(%rip), %rax
    jne C(dearust_rpc_leaf_cancel)
    movl 8(%rsp), %eax
    movq %rcx, %r10
    syscall
C(dearust_rpc_leaf_end):
    ret
C(dearust_rpc_leaf_cancel):
    movq $-4096, %rax
    ret
