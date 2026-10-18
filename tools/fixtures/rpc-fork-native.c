/* Native ABI-independent transaction guard checks. No original RPC mocks. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <ucontext.h>
#include <unistd.h>
#define linux_msghdr msghdr
#define linux_cmsghdr cmsghdr
#define LINUX_CMSG_LEN CMSG_LEN
#define LINUX_SOL_SOCKET SOL_SOCKET
#define DSERVER_RPC_HOOKS_CMSG_DATA CMSG_DATA
typedef unsigned long linux_sigset_t;
static int fail_mask_call;
#define LINUX_SYSCALL(number,...) ((number)==SYS_rt_sigprocmask && fail_mask_call ? -EPERM : syscall(number,__VA_ARGS__))
#include "rpc-fork-guard.h"
extern void dearust_rpc_fork_child(ucontext_t *);
extern char dearust_rpc_leaf_begin,dearust_rpc_leaf_end,dearust_rpc_leaf_cancel;
static int sockets[2];
static pthread_t target;
static volatile sig_atomic_t once,child_return,child_pid;
static void handler(int signal,siginfo_t *info,void *opaque){
    (void)info;assert(signal==SIGUSR1 && !once);once=1;
    ucontext_t *context=opaque;
    assert((unsigned long)context->uc_mcontext.gregs[REG_RIP]>=(unsigned long)&dearust_rpc_leaf_begin);
    assert((unsigned long)context->uc_mcontext.gregs[REG_RIP]<(unsigned long)&dearust_rpc_leaf_end);
    pid_t pid=fork();assert(pid>=0);
    if(pid==0){dearust_rpc_fork_child(context);child_return=1;}else child_pid=pid;
}
static void *sender(void *unused){
    (void)unused;usleep(10000);assert(pthread_kill(target,SIGUSR1)==0);
    usleep(10000);assert(send(sockets[1],"x",1,0)==1);return NULL;
}
static void rights_cleanup(void){
    int fd=open("/dev/null",O_RDONLY);assert(fd>=0);char byte='x';struct iovec io={&byte,1};
    union {struct cmsghdr alignment;char bytes[CMSG_SPACE(2*sizeof(int))];} control={0};
    struct msghdr message={.msg_iov=&io,.msg_iovlen=1,.msg_control=control.bytes,.msg_controllen=sizeof(control)};
    struct cmsghdr *header=CMSG_FIRSTHDR(&message);header->cmsg_level=SOL_SOCKET;header->cmsg_type=SCM_RIGHTS;header->cmsg_len=CMSG_LEN(2*sizeof(int));
    int pair[2]={fd,fd};memcpy(CMSG_DATA(header),pair,sizeof(pair));assert(sendmsg(sockets[1],&message,0)==1);
    memset(&control,0,sizeof(control));message.msg_controllen=sizeof(control);assert(recvmsg(sockets[0],&message,0)==1);
    memcpy(pair,CMSG_DATA(CMSG_FIRSTHDR(&message)),sizeof(pair));assert(pair[0]>=0 && pair[1]>=0 && pair[0]!=pair[1]);
    dearust_rpc_discard_rights(&message,sizeof(control));
    assert(message.msg_controllen==0 && fcntl(pair[0],F_GETFD)==-1 && errno==EBADF);
    assert(fcntl(pair[1],F_GETFD)==-1 && errno==EBADF && fcntl(fd,F_GETFD)>=0);assert(close(fd)==0);
}
static void mask_boundaries(void){
    linux_sigset_t original, saved, current;
    assert(syscall(SYS_rt_sigprocmask,0,NULL,&original,8)==0);
    linux_sigset_t with_rt=original | (1UL << (SIGRTMAX-1));
    assert(syscall(SYS_rt_sigprocmask,2,&with_rt,NULL,8)==0);
    dearust_rpc_mask_begin(&saved);
    assert(saved==with_rt && syscall(SYS_rt_sigprocmask,0,NULL,&current,8)==0);
    unsigned long blocked=0x7fffffffUL & ~(1UL << (SIGKILL-1)) & ~(1UL << (SIGSTOP-1));
    assert((current & 0x7fffffffUL)==blocked && (current & (1UL << (SIGRTMAX-1))));
    dearust_rpc_mask_end(&saved);
    assert(syscall(SYS_rt_sigprocmask,0,NULL,&current,8)==0 && current==with_rt);
    assert(syscall(SYS_rt_sigprocmask,2,&original,NULL,8)==0);
    for(int restore=0;restore<2;restore++){
        pid_t pid=fork();assert(pid>=0);
        if(pid==0){fail_mask_call=1;if(restore)dearust_rpc_mask_end(&original);else dearust_rpc_mask_begin(&saved);_exit(99);}
        int status;assert(waitpid(pid,&status,0)==pid && WIFEXITED(status) && WEXITSTATUS(status)==127);
    }
}
int main(void){
    alarm(10);mask_boundaries();assert(socketpair(AF_UNIX,SOCK_DGRAM,0,sockets)==0);
    unsigned long generation=dearust_rpc_current_generation();char byte='x';struct iovec io={&byte,1};struct msghdr msg={.msg_iov=&io,.msg_iovlen=1};
    dearust_rpc_generation=generation+1;
    assert(dearust_rpc_syscall(sockets[1],(long)&msg,0,0,0,0,SYS_sendmsg,generation)==-4096);
    assert(recv(sockets[0],&byte,1,MSG_DONTWAIT)==-1 && errno==EAGAIN);
    assert(dearust_rpc_abandoned(generation));dearust_rpc_generation=generation;
    /* Every leaf PC has the same stack shape. Its return PC denotes a
     * completed syscall and must preserve the inherited non-EINTR response. */
    for(unsigned long pc=(unsigned long)&dearust_rpc_leaf_begin;pc<=(unsigned long)&dearust_rpc_leaf_end;pc++){
        ucontext_t context={0};context.uc_mcontext.gregs[REG_RIP]=(greg_t)pc;context.uc_mcontext.gregs[REG_RAX]=1;
        dearust_rpc_fork_child(&context);
        assert((unsigned long)context.uc_mcontext.gregs[REG_RIP]==(pc<(unsigned long)&dearust_rpc_leaf_end?(unsigned long)&dearust_rpc_leaf_cancel:pc));
        assert(context.uc_mcontext.gregs[REG_RAX]==1);
    }
    dearust_rpc_generation=generation;
    rights_cleanup();
    struct sigaction action={0};action.sa_sigaction=handler;action.sa_flags=SA_SIGINFO|SA_RESTART;sigemptyset(&action.sa_mask);assert(sigaction(SIGUSR1,&action,NULL)==0);
    target=pthread_self();pthread_t worker;assert(pthread_create(&worker,NULL,sender,NULL)==0);
    long result=dearust_rpc_syscall(sockets[0],(long)&msg,0,0,0,0,SYS_recvmsg,generation);
    if(child_return){assert(result==-4096 && dearust_rpc_abandoned(generation));_exit(0);}
    assert(result==1 && byte=='x' && !dearust_rpc_abandoned(generation));
    assert(pthread_join(worker,NULL)==0 && child_pid>0);int status;
    assert(waitpid(child_pid,&status,0)==child_pid && WIFEXITED(status) && WEXITSTATUS(status)==0);
    assert(close(sockets[0])==0 && close(sockets[1])==0);
    puts("RPC fork guard: stale sends, rights ownership and restarted receive child cancellation passed");return 0;
}
