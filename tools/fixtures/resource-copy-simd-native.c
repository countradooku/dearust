/* SPDX-License-Identifier: GPL-3.0-or-later
 * Native exact-range binary-copy regression. No RPC or resource ownership. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <ucontext.h>
#include <unistd.h>
extern long dearust_copy_guarded(void *,const void *,size_t,size_t);
extern long dearust_copyin(void *,const void *,size_t);
extern long dearust_copyout(const void *,void *,size_t);
extern uintptr_t dearust_copy_signal_fixup(uintptr_t,int,int);
extern char dearust_copy_guest_simd_read,dearust_copy_guest_simd_write;
extern char dearust_copy_guest_binary_read,dearust_copy_guest_write;
extern char dearust_copy_region_begin,dearust_copy_region_end;
extern char dearust_copy_error,dearust_copy_fallback;
static int ready=1;
static volatile uintptr_t fault_pc;
int dearust_copy_signals_ready(void){return ready;}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){
    long value=syscall(nr,a,b,c,d,e,f);return value<0?-errno:value;
}
static void fault(int signal,siginfo_t *info,void *opaque){
    ucontext_t *context=opaque;
    uintptr_t pc=(uintptr_t)context->uc_mcontext.gregs[REG_RIP];
    uintptr_t next=dearust_copy_signal_fixup(pc,signal,info->si_code);
    if(!next)_exit(128+signal);
    fault_pc=pc;context->uc_mcontext.gregs[REG_RIP]=(greg_t)next;
}
static uint64_t mask(void){uint64_t value=0;assert(syscall(SYS_rt_sigprocmask,0,0,&value,8)==0);return value;}
static unsigned char *mapping(void){
    unsigned char *value=mmap(0,12288,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    assert(value!=MAP_FAILED);assert(mprotect(value+4096,4096,PROT_READ|PROT_WRITE)==0);return value;
}
static void fixups(void){
    for(uintptr_t pc=(uintptr_t)&dearust_copy_region_begin;pc<(uintptr_t)&dearust_copy_region_end;pc++){
        int access=pc==(uintptr_t)&dearust_copy_guest_simd_read||pc==(uintptr_t)&dearust_copy_guest_simd_write||pc==(uintptr_t)&dearust_copy_guest_binary_read||pc==(uintptr_t)&dearust_copy_guest_write;
        /* Path load is separately covered by the existing fixture. */
        uintptr_t sync=dearust_copy_signal_fixup(pc,SIGSEGV,SEGV_MAPERR);
        if(access)assert(sync==(uintptr_t)&dearust_copy_error);
        assert(dearust_copy_signal_fixup(pc,SIGUSR1,SI_USER)==(uintptr_t)&dearust_copy_fallback);
    }
}
int main(void){
    struct sigaction action={0};action.sa_sigaction=fault;action.sa_flags=SA_SIGINFO;sigemptyset(&action.sa_mask);
    assert(sigaction(SIGSEGV,&action,0)==0&&sigaction(SIGBUS,&action,0)==0);
    uint64_t initial=mask();fixups();
    unsigned char *source=mapping(),*target=mapping();
    unsigned char *in=source+4096,*out=target+4096;
    for(size_t i=0;i<4096;i++)in[i]=(unsigned char)(i*37+11);
    const size_t lengths[]={0,4,8,12,16,20,32,48,120,128,144,256,4096};
    unsigned cases=0;
    for(size_t k=0;k<sizeof(lengths)/sizeof(lengths[0]);k++){
        size_t n=lengths[k];
        for(size_t shift=0;shift<16&&n+shift<=4096;shift++){
            size_t offset=4096-n-shift;memset(out,0xa5,4096);
            assert(dearust_copy_guarded(out+offset,in+offset,n,2)==0);
            assert(!memcmp(out+offset,in+offset,n));
            for(size_t i=0;i<offset;i++)assert(out[i]==0xa5);
            for(size_t i=offset+n;i<4096;i++)assert(out[i]==0xa5);
            memset(out,0xa5,4096);
            assert(dearust_copy_guarded(out+offset,in+offset,n,0)==0);
            assert(!memcmp(out+offset,in+offset,n));
            for(size_t i=0;i<offset;i++)assert(out[i]==0xa5);
            for(size_t i=offset+n;i<4096;i++)assert(out[i]==0xa5);
            cases+=2;
        }
    }
    for(size_t n=16;n<=144;n+=128){
        fault_pc=0;assert(dearust_copy_guarded(out,source,n,2)==-14);
        assert(fault_pc==(uintptr_t)&dearust_copy_guest_simd_read);
        fault_pc=0;assert(dearust_copy_guarded(target,in,n,0)==-14);
        assert(fault_pc==(uintptr_t)&dearust_copy_guest_simd_write);
        assert(dearust_copy_guarded(out,in+4096-n+1,n,2)==-14);
        assert(dearust_copy_guarded(out+4096-n+1,in,n,0)==-14);
    }
    fault_pc=0;assert(dearust_copy_guarded(out,source,4,2)==-14);
    assert(fault_pc==(uintptr_t)&dearust_copy_guest_binary_read);
    fault_pc=0;assert(dearust_copy_guarded(target,in,4,0)==-14);
    assert(fault_pc==(uintptr_t)&dearust_copy_guest_write);
    FILE *empty=tmpfile();assert(empty);
    unsigned char *bus=mmap(0,4096,PROT_READ|PROT_WRITE,MAP_SHARED,fileno(empty),0);
    assert(bus!=MAP_FAILED);
    fault_pc=0;assert(dearust_copy_guarded(out,bus,144,2)==-14);
    assert(fault_pc==(uintptr_t)&dearust_copy_guest_simd_read);
    fault_pc=0;assert(dearust_copy_guarded(bus,in,144,0)==-14);
    assert(fault_pc==(uintptr_t)&dearust_copy_guest_simd_write);
    assert(munmap(bus,4096)==0&&fclose(empty)==0);
    for(size_t n=1;n<16;n++)if(n%4)assert(dearust_copy_guarded(out,in,n,2)==-4096);
    uint64_t blocked=initial|(1ULL<<(SIGSEGV-1))|(1ULL<<(SIGBUS-1));
    assert(syscall(SYS_rt_sigprocmask,SIG_SETMASK,&blocked,0,8)==0);
    assert(dearust_copy_guarded(out,source,144,2)==-4096);
    assert(dearust_copy_guarded(target,in,144,0)==-4096);
    assert(dearust_copyin(out,source,144)==-14&&dearust_copyout(in,target,144)==-14);
    assert(mask()==blocked);
    assert(syscall(SYS_rt_sigprocmask,SIG_SETMASK,&initial,0,8)==0);
    ready=0;assert(dearust_copyin(out,in,144)==0&&dearust_copyout(in,out,144)==0);
    assert(dearust_copyin(out,source,144)==-14&&dearust_copyout(in,target,144)==-14);
    assert(mask()==initial);assert(munmap(source,12288)==0&&munmap(target,12288)==0);
    printf("%u exact-range cases; SIMD fault PCs, mask and readiness fallback pass\n",cases);
    return 0;
}
