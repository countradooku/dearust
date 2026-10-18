/* Native-only guarded-copy adversarial tests. The included pointer fixture
 * covers the exported path/metadata ABI using opaque converter output sizes. */
#define main pointer_boundary_main
#define linux_syscall pointer_linux_syscall
#include "pointer-native.c"
#undef linux_syscall
#undef main
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <ucontext.h>

extern long dearust_copy_guarded(void *,const void *,size_t,size_t);
extern unsigned long dearust_copy_signal_fixup(unsigned long,int,int);
extern char dearust_copy_region_begin,dearust_copy_region_end;
extern char dearust_copy_guest_read,dearust_copy_guest_write,dearust_copy_guest_binary_read;
extern long dearust_copyin(void *,const void *,size_t);
extern long dearust_copyout(const void *,void *,size_t);
extern char dearust_copy_error,dearust_copy_fallback;
extern int dearust_copy_test_set_handler(int,const struct sigaction *,struct sigaction *);
#ifdef DEARUST_TEST_WRAP_READY
extern int __real_dearust_copy_signals_ready(void);
static int force_kernel_copy;
int __wrap_dearust_copy_signals_ready(void){return force_kernel_copy?0:__real_dearust_copy_signals_ready();}
#endif
static int inject_restore_error;
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){
    if(inject_restore_error && nr==SYS_rt_sigprocmask && a==SIG_SETMASK)return -EPERM;
    return pointer_linux_syscall(a,b,c,d,e,f,nr);
}
/* Set alignment checking only around the leaf, with no intervening C/library
 * code. Return both its result and the still-set caller flags, restoring the
 * original flags before returning to compiler-generated code. */
struct alignment_result { long status; unsigned long flags; };
extern struct alignment_result copy_with_alignment_check(void *,const void *,size_t,size_t);
__asm__(".text\n"
        ".type copy_with_alignment_check,@function\n"
        "copy_with_alignment_check:\n"
        "pushfq\n"
        "pushfq\n"
        "orq $0x40000,(%rsp)\n"
        "popfq\n"
        "call dearust_copy_guarded\n"
        "pushfq\n"
        "pop %rdx\n"
        "popfq\n"
        "ret\n"
        ".size copy_with_alignment_check,.-copy_with_alignment_check\n");
static void alignment_check_fallback(void){
    unsigned char input[160],output[160];memset(input,0,sizeof(input));memset(output,0xa5,sizeof(output));
    struct alignment_result result=copy_with_alignment_check(output+1,input+1,144,0);
    assert(result.status==-4096 && (result.flags&0x40000));
    result=copy_with_alignment_check(output+1,input+1,5,1);
    assert(result.status==-4096 && (result.flags&0x40000));
    result=copy_with_alignment_check(output+1,input+1,16,2);
    assert(result.status==-4096 && (result.flags&0x40000));
    for(size_t i=0;i<sizeof(output);i++)assert(output[i]==0xa5);
    unsigned long restored;__asm__ volatile("pushfq; pop %0":"=r"(restored));
    assert(!(restored&0x40000));
}
static int owned_fd;
static volatile sig_atomic_t nested, forked, child_return, child_pid;
static volatile sig_atomic_t interrupted;
static _Atomic int stop_sender, stop_churn;
static pthread_t main_thread;
static char *churn_page;
static unsigned char big_input[1024*1024],big_output[1024*1024];
static unsigned long address(char *symbol){return (unsigned long)symbol;}
static unsigned long mask(void){unsigned long value=0;assert(syscall(SYS_rt_sigprocmask,0,NULL,&value,8)==0);return value;}
static void exact_fixups(void){
    unsigned long begin=address(&dearust_copy_region_begin),end=address(&dearust_copy_region_end);
    for(unsigned long pc=begin;pc<end;pc++){
        int access=pc==address(&dearust_copy_guest_read)||pc==address(&dearust_copy_guest_write)||pc==address(&dearust_copy_guest_binary_read);
        assert(dearust_copy_signal_fixup(pc,SIGSEGV,SEGV_MAPERR)==(access?address(&dearust_copy_error):0));
        assert(dearust_copy_signal_fixup(pc,SIGBUS,BUS_ADRERR)==(access?address(&dearust_copy_error):0));
        assert(dearust_copy_signal_fixup(pc,SIGUSR1,SI_USER)==address(&dearust_copy_fallback));
        assert(dearust_copy_signal_fixup(pc,SIGCHLD,CLD_EXITED)==address(&dearust_copy_fallback));
        assert(dearust_copy_signal_fixup(pc,SIGTTOU,SI_USER)==address(&dearust_copy_fallback));
        assert(dearust_copy_signal_fixup(pc,SIGSEGV,SI_USER)==address(&dearust_copy_fallback));
    }
    assert(dearust_copy_signal_fixup(begin-1,SIGUSR1,SI_USER)==0);
    assert(dearust_copy_signal_fixup(end,SIGUSR1,SI_USER)==0);
    assert(dearust_copy_signal_fixup(1,SIGSEGV,SEGV_MAPERR)==0);
}
static void nested_handler(int signal,siginfo_t *info,void *opaque){
    (void)signal;(void)info;(void)opaque;
    unsigned long before=mask();unsigned char output[144];
    assert((before & ((1UL<<(SIGSEGV-1))|(1UL<<(SIGBUS-1))))!=0);
    assert(sys_fstat64(owned_fd,output)==0);
    assert(sys_fstat64(owned_fd,(void *)(uintptr_t)1)==-14);
    assert(sys_stat64((void *)(uintptr_t)1,output)==-14);
    assert(mask()==before);nested++;
}
static void fork_handler(int signal,siginfo_t *info,void *opaque){
    (void)signal;(void)info;
    ucontext_t *context=opaque;
    /* The early wrapper has already redirected an interrupted assembly copy.
     * Fork only at this deterministic proof point; ordinary delivery retries. */
    if(context->uc_mcontext.gregs[REG_RIP]!=(greg_t)address(&dearust_copy_fallback)||forked)return;
    interrupted=1;forked=1;
    pid_t pid=fork();assert(pid>=0);
    if(pid==0)child_return=1;else child_pid=pid;
}
static void *sender(void *unused){
    (void)unused;
    while(!atomic_load_explicit(&stop_sender,memory_order_acquire)){
        assert(pthread_kill(main_thread,SIGUSR2)==0);usleep(50);
    }
    return NULL;
}
static void unrelated_fault(int signal,siginfo_t *info,void *opaque){
    (void)opaque;
    _exit(signal==SIGSEGV && info->si_code>0?0:90);
}
static void *churn(void *unused){
    (void)unused;
    while(!atomic_load_explicit(&stop_churn,memory_order_acquire)){
        assert(mprotect(churn_page,4096,PROT_NONE)==0);
        assert(mprotect(churn_page,4096,PROT_READ|PROT_WRITE)==0);
    }
    return NULL;
}
static void binary_copy_boundaries(void){
    unsigned long before=mask();
    unsigned char input[160],output[160];
    for(size_t i=0;i<sizeof(input);i++)input[i]=(unsigned char)(i*37);
    const size_t lengths[]={0,1,3,4,8,16,144};
    for(size_t i=0;i<sizeof(lengths)/sizeof(lengths[0]);i++){
        size_t length=lengths[i];memset(output,0xa5,sizeof(output));
        assert(dearust_copyin(output+1,input+1,length)==0);
        assert(!memcmp(output+1,input+1,length));
        assert(output[0]==0xa5 && output[length+1]==0xa5);
        memset(output,0xa5,sizeof(output));
        assert(dearust_copyout(input+1,output+1,length)==0);
        assert(!memcmp(output+1,input+1,length));
        assert(output[0]==0xa5 && output[length+1]==0xa5);
    }
    assert(dearust_copyin(output,NULL,0)==0);
    assert(dearust_copyout(input,NULL,0)==0);
    assert(dearust_copyin(output,NULL,16)==-14);
    assert(dearust_copyout(input,NULL,16)==-14);
    unsigned char *pages=mmap(NULL,8192,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    assert(pages!=MAP_FAILED);memset(pages,0x39,8192);
    assert(mprotect(pages+4096,4096,PROT_NONE)==0);
    memset(output,0xa5,sizeof(output));
    assert(dearust_copyin(output,pages+4088,16)==-14);
    assert(output[16]==0xa5);
    assert(dearust_copyout(input,pages+4088,16)==-14);
    assert(dearust_copyin(output,pages+4096,16)==-14);
    assert(dearust_copyout(input,pages+4096,16)==-14);
    assert(mprotect(pages,4096,PROT_READ)==0);
    assert(dearust_copyin(output,pages,16)==0);
    assert(dearust_copyout(input,pages,16)==-14);
    assert(munmap(pages,8192)==0);assert(mask()==before);
}
int main(void){
    alarm(30);assert(pointer_boundary_main()==0);exact_fixups();alignment_check_fallback();binary_copy_boundaries();
#ifdef DEARUST_TEST_WRAP_READY
    force_kernel_copy=1;conversions=0;assert(pointer_boundary_main()==0);binary_copy_boundaries();force_kernel_copy=0;
#endif
    char file[]="/tmp/dearust-guarded-XXXXXX";owned_fd=mkstemp(file);assert(owned_fd>=0);
    unsigned long original=mask();sigset_t blocked;sigemptyset(&blocked);sigaddset(&blocked,SIGSEGV);sigaddset(&blocked,SIGBUS);
    assert(pthread_sigmask(SIG_BLOCK,&blocked,NULL)==0);unsigned long both=mask();
    unsigned char out[144];
    assert(dearust_copy_guarded(out,"safe",5,1)==-4096);
    assert(sys_fstat64(owned_fd,out)==0 && mask()==both);
    assert(sys_fstat64(owned_fd,(void *)(uintptr_t)1)==-14 && mask()==both);
    assert(sys_stat64((void *)(uintptr_t)1,out)==-14 && mask()==both);
    assert(syscall(SYS_rt_sigprocmask,SIG_SETMASK,&original,NULL,8)==0);
#ifdef DEARUST_TEST_WRAP_READY
    /* If restoring the exact mask fails, continuing with signals suppressed is
     * invalid. Inject the failure only in an owned child and require fail-stop. */
    pid_t restore_child=fork();assert(restore_child>=0);
    if(restore_child==0){force_kernel_copy=1;inject_restore_error=1;(void)sys_fstat64(owned_fd,out);_exit(92);}
    int restore_status;assert(waitpid(restore_child,&restore_status,0)==restore_child);
    assert(WIFEXITED(restore_status) && WEXITSTATUS(restore_status)==127);
#endif
    /* A truncated file mapping raises actual SIGBUS, distinct from SIGSEGV. */
    char bus_file[]="/tmp/dearust-guarded-bus-XXXXXX";int bus_fd=mkstemp(bus_file);assert(bus_fd>=0);
    char *bus=mmap(NULL,4096,PROT_READ,MAP_PRIVATE,bus_fd,0);assert(bus!=MAP_FAILED);
    assert(dearust_copy_guarded(out,bus,144,1)==-14);
    assert(munmap(bus,4096)==0 && close(bus_fd)==0 && unlink(bus_file)==0);
    struct sigaction action={0};action.sa_sigaction=nested_handler;action.sa_flags=SA_SIGINFO;
    sigemptyset(&action.sa_mask);sigaddset(&action.sa_mask,SIGSEGV);sigaddset(&action.sa_mask,SIGBUS);
    assert(dearust_copy_test_set_handler(SIGUSR1,&action,NULL)==0);
    assert(dearust_copy_test_set_handler(SIGCHLD,&action,NULL)==0);
    assert(dearust_copy_test_set_handler(SIGTTOU,&action,NULL)==0);
    assert(raise(SIGUSR1)==0 && raise(SIGCHLD)==0 && raise(SIGTTOU)==0);
    assert(nested==3 && mask()==original);
    /* Unrelated synchronous faults must reach the user/default dispatch. */
    pid_t child=fork();assert(child>=0);
    if(child==0){action.sa_sigaction=unrelated_fault;sigemptyset(&action.sa_mask);assert(dearust_copy_test_set_handler(SIGSEGV,&action,NULL)==0);*(volatile char *)(uintptr_t)1=0;_exit(91);}
    int status;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
    action.sa_sigaction=fork_handler;sigemptyset(&action.sa_mask);
    assert(dearust_copy_test_set_handler(SIGUSR2,&action,NULL)==0);
    memset(big_input,0x5b,sizeof(big_input));main_thread=pthread_self();pthread_t worker;
    assert(pthread_create(&worker,NULL,sender,NULL)==0);
    for(unsigned count=0;count<10000 && !forked;count++){
        long result=dearust_copy_guarded(big_output,big_input,sizeof(big_input),0);
        assert(result==0 || result==-4096);
        if(child_return){unsigned char child_out[144];assert(result==-4096);assert(sys_fstat64(owned_fd,child_out)==0);assert(sys_fstat64(owned_fd,(void *)(uintptr_t)1)==-14);_exit(0);}
    }
    atomic_store_explicit(&stop_sender,1,memory_order_release);assert(pthread_join(worker,NULL)==0);
    assert(forked && interrupted && child_pid>0);
    assert(waitpid(child_pid,&status,0)==child_pid && WIFEXITED(status) && WEXITSTATUS(status)==0);
    assert(mask()==original);
    /* Concurrent permission revocation must yield success or EFAULT, never a
     * swallowed unrelated fault or dereference through a Rust reference. */
    churn_page=mmap(NULL,4096,PROT_READ|PROT_WRITE,MAP_ANONYMOUS|MAP_PRIVATE,-1,0);assert(churn_page!=MAP_FAILED);
    memset(churn_page,'x',4096);assert(pthread_create(&worker,NULL,churn,NULL)==0);
    for(unsigned count=0;count<10000;count++){
        long result=dearust_copy_guarded(big_output,churn_page,4096,1);assert(result==-14 || result==-63);
        result=dearust_copy_guarded(churn_page,big_input,144,0);assert(result==-14 || result==0);
    }
    atomic_store_explicit(&stop_churn,1,memory_order_release);assert(pthread_join(worker,NULL)==0);
    assert(munmap(churn_page,4096)==0 && close(owned_fd)==0 && unlink(file)==0);
    puts("Guarded copy: exact PCs, blocked masks, nested signals, interrupted handler fork, unrelated faults and concurrent protection changes passed");return 0;
}
