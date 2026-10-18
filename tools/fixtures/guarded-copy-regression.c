/* Guest real Darwin ABI coverage for guarded copies and kernel-copy fallback.
 * Includes the existing exhaustive exported pointer boundary checks. */
#define main pointer_boundary_main
#include "pointer-regression.c"
#undef main
#include <pthread.h>
#include <signal.h>

static int owned_fd;
static volatile sig_atomic_t nested_count,fork_requested,forked_child,child_pid;
static volatile sig_atomic_t sender_stop,nodefer_depth,nodefer_count;
static pthread_t target;
static void current_mask(sigset_t *value){assert(pthread_sigmask(SIG_BLOCK,NULL,value)==0);}
static void same_mask(const sigset_t *one,const sigset_t *two){
    for(int signal=1;signal<NSIG;signal++)assert(sigismember(one,signal)==sigismember(two,signal));
}
static void nested_handler(int signal){
    (void)signal;sigset_t before,after;current_mask(&before);
    assert(sigismember(&before,SIGSEGV)==1 && sigismember(&before,SIGBUS)==1);
    assert(sigismember(&before,SIGUSR2)==1);
    union metadata result;
    assert(syscall(SYS_fstat64,owned_fd,&result)==0 && result.wide.st_size==7);
    errno=0;assert(syscall(SYS_fstat64,owned_fd,(void *)(uintptr_t)1)==-1 && errno==EFAULT);
    errno=0;assert(syscall(SYS_stat64,(void *)(uintptr_t)1,&result)==-1 && errno==EFAULT);
    current_mask(&after);same_mask(&before,&after);nested_count++;
}
/* SA_NODEFER is a Darwin flag; the guest signal must remain unblocked while
 * preserving the unrelated mask inherited from the interrupted context. */
static void nodefer_handler(int signal){
    assert(signal==SIGUSR1);sigset_t value;current_mask(&value);
    assert(sigismember(&value,SIGUSR1)==0 && sigismember(&value,SIGUSR2)==1);
    nodefer_count++;nodefer_depth++;
    if(nodefer_depth==1)assert(raise(SIGUSR1)==0);
    assert(nodefer_depth<=2);nodefer_depth--;
}
static void fork_handler(int signal){
    (void)signal;if(fork_requested)return;fork_requested=1;
    pid_t pid=fork();assert(pid>=0);if(pid==0)forked_child=1;else child_pid=pid;
}
static void unrelated_fault(int signal){_exit(signal==SIGSEGV?0:90);}
static void *sender(void *unused){
    (void)unused;for(unsigned index=0;index<200 && !__atomic_load_n(&sender_stop,__ATOMIC_ACQUIRE);index++){
        assert(pthread_kill(target,SIGUSR2)==0);usleep(100);
    }
    return NULL;
}
static void *metadata_worker(void *unused){
    (void)unused;for(unsigned i=0;i<1000;i++){
        union metadata result;assert(syscall(SYS_fstat64,owned_fd,&result)==0 && result.wide.st_size==7);
        errno=0;assert(syscall(SYS_fstat64,owned_fd,(void *)(uintptr_t)1)==-1 && errno==EFAULT);
    }
    return NULL;
}
int main(void){
    alarm(60);assert(pointer_boundary_main()==0);
    char file[]="/private/tmp/dearust-guarded-XXXXXX";owned_fd=mkstemp(file);
    assert(owned_fd>=0 && write(owned_fd,"payload",7)==7);
    sigset_t original,blocked,before,after;current_mask(&original);
    sigemptyset(&blocked);sigaddset(&blocked,SIGSEGV);sigaddset(&blocked,SIGBUS);
    assert(pthread_sigmask(SIG_BLOCK,&blocked,NULL)==0);current_mask(&before);
    union metadata result;assert(syscall(SYS_fstat64,owned_fd,&result)==0 && result.wide.st_size==7);
    errno=0;assert(syscall(SYS_fstat64,owned_fd,(void *)(uintptr_t)1)==-1 && errno==EFAULT);
    errno=0;assert(syscall(SYS_stat64,(void *)(uintptr_t)1,&result)==-1 && errno==EFAULT);
    current_mask(&after);same_mask(&before,&after);assert(pthread_sigmask(SIG_SETMASK,&original,NULL)==0);
    struct sigaction action,old_usr1,old_chld,old_ttou;
    memset(&action,0,sizeof(action));action.sa_handler=nested_handler;action.sa_mask=blocked;
    assert(sigaction(SIGUSR1,&action,&old_usr1)==0 && sigaction(SIGCHLD,&action,&old_chld)==0 && sigaction(SIGTTOU,&action,&old_ttou)==0);
    /* Mask bits stored by sys_sigaction are Darwin bits, while the Linux
     * handler must use translated sa_mask union the prior interrupted mask. */
    sigset_t unrelated;sigemptyset(&unrelated);sigaddset(&unrelated,SIGUSR2);
    assert(pthread_sigmask(SIG_BLOCK,&unrelated,NULL)==0);current_mask(&before);
    assert(raise(SIGUSR1)==0 && raise(SIGCHLD)==0 && raise(SIGTTOU)==0 && nested_count==3);
    current_mask(&after);same_mask(&before,&after);
    action.sa_handler=nodefer_handler;sigemptyset(&action.sa_mask);action.sa_flags=SA_NODEFER;
    assert(sigaction(SIGUSR1,&action,NULL)==0 && raise(SIGUSR1)==0);
    assert(nodefer_count==2 && nodefer_depth==0);
    current_mask(&after);same_mask(&before,&after);
    /* Later child exits legitimately arrive with the original SIGUSR2 mask.
     * End the scoped inherited-mask assertions before those unrelated events. */
    assert(sigaction(SIGUSR1,&old_usr1,NULL)==0 && sigaction(SIGCHLD,&old_chld,NULL)==0 && sigaction(SIGTTOU,&old_ttou,NULL)==0);
    assert(pthread_sigmask(SIG_SETMASK,&original,NULL)==0);
    current_mask(&after);same_mask(&original,&after);
    action.sa_flags=0;
    /* An ordinary guest synchronous fault must still reach its handler. */
    pid_t child=fork();assert(child>=0);
    if(child==0){
        /* The original compatibility reporter prints variable address details
         * for this intentional fault. Silence only this child; the asserted
         * handler exit status still proves unrelated SIGSEGV is dispatched. */
        int quiet=open("/dev/null",O_WRONLY);assert(quiet>=0 && dup2(quiet,STDOUT_FILENO)==STDOUT_FILENO && close(quiet)==0);
        action.sa_handler=unrelated_fault;sigemptyset(&action.sa_mask);assert(sigaction(SIGSEGV,&action,NULL)==0);*(volatile char *)(uintptr_t)1=0;_exit(91);}
    int status;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
    action.sa_handler=fork_handler;sigemptyset(&action.sa_mask);assert(sigaction(SIGUSR2,&action,NULL)==0);
    target=pthread_self();pthread_t signal_worker,copy_worker;
    assert(pthread_create(&copy_worker,NULL,metadata_worker,NULL)==0);
    assert(pthread_create(&signal_worker,NULL,sender,NULL)==0);
    for(unsigned i=0;i<20000;i++){
        assert(syscall(SYS_fstat64,owned_fd,&result)==0 && result.wide.st_size==7);
        if(forked_child){
            assert(syscall(SYS_fstat64,owned_fd,&result)==0 && result.wide.st_size==7);
            errno=0;assert(syscall(SYS_fstat64,owned_fd,(void *)(uintptr_t)1)==-1 && errno==EFAULT);_exit(0);
        }
        if(fork_requested)break;
    }
    __atomic_store_n(&sender_stop,1,__ATOMIC_RELEASE);
    assert(pthread_join(signal_worker,NULL)==0 && pthread_join(copy_worker,NULL)==0);
    assert(fork_requested && child_pid>0);
    assert(waitpid(child_pid,&status,0)==child_pid && WIFEXITED(status) && WEXITSTATUS(status)==0);
    current_mask(&after);same_mask(&original,&after);
    assert(close(owned_fd)==0 && unlink(file)==0);
    puts("Rust guarded copy signal, mask, concurrency and fork regression checks passed");return 0;
}
