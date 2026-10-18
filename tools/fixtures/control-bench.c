/* Warm process-control/error paths in an owned child, not session creation throughput. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <darling/emulation/xnu_syscall/bsd/impl/misc/getrlimit.h>
#include <darling/emulation/conversion/errno.h>
extern long sys_setpgid(int,int),sys_setsid(void),sys_umask(unsigned),sys_getdtablesize(void);
_Static_assert(sizeof(struct xnu_rlimit)==176,"xnu rlimit storage");
_Static_assert(BSD_RLIMIT_NOFILE==8,"Darwin NOFILE constant");
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){long r=syscall(nr,a,b,c,d,e,f);return r<0?-errno:r;}
long sys_getrlimit(unsigned which,struct xnu_rlimit *out){
    assert(which==BSD_RLIMIT_NOFILE);struct rlimit host;
    if(getrlimit(RLIMIT_NOFILE,&host)<0)return errno_linux_to_bsd(-errno);
    out->rlim_cur=(uint64_t)host.rlim_cur-1;out->rlim_max=(uint64_t)host.rlim_max-1;return 0;
}
static uint64_t nanos(clockid_t clock){struct timespec t;assert(clock_gettime(clock,&t)==0);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void roundtrip(long expected){assert(sys_setpgid(0,0)==0);assert(sys_setsid()==-1);assert(sys_umask(022)==022);assert(sys_getdtablesize()==expected);}
int main(int argc,char **argv){
    assert(argc==2);unsigned n=(unsigned)strtoul(argv[1],NULL,10);assert(n>0);
    int channel[2];assert(pipe(channel)==0);pid_t parent=getpid(),group=getpgrp(),session=getsid(0);
    pid_t child=fork();assert(child>=0);
    if(child==0){
        assert(close(channel[0])==0);mode_t inherited=umask(022);
        assert(write(channel[1],&inherited,sizeof(inherited))==sizeof(inherited) && close(channel[1])==0);
        assert(sys_setpgid(0,0)==0);struct rlimit host;assert(getrlimit(RLIMIT_NOFILE,&host)==0);
        uint64_t soft=(uint64_t)host.rlim_cur-1;long expected=soft>INT_MAX?INT_MAX:(long)soft;
        for(unsigned i=0;i<100;i++)roundtrip(expected);
        uint64_t c=nanos(CLOCK_THREAD_CPUTIME_ID),w=nanos(CLOCK_MONOTONIC);
        for(unsigned i=0;i<n;i++)roundtrip(expected);
        w=nanos(CLOCK_MONOTONIC)-w;c=nanos(CLOCK_THREAD_CPUTIME_ID)-c;
        printf("%u %llu %llu\n",n,(unsigned long long)c,(unsigned long long)w);assert(fflush(stdout)==0);_exit(0);
    }
    assert(close(channel[1])==0);mode_t original,after;
    assert(read(channel[0],&original,sizeof(original))==sizeof(original) && close(channel[0])==0);
    int status;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
    assert(pipe(channel)==0);child=fork();assert(child>=0);
    if(child==0){close(channel[0]);mode_t mask=umask(022);assert(write(channel[1],&mask,sizeof(mask))==sizeof(mask));_exit(0);}
    assert(close(channel[1])==0 && read(channel[0],&after,sizeof(after))==sizeof(after) && close(channel[0])==0);
    assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
    assert(original==after && getpid()==parent && getpgrp()==group && getsid(0)==session);return 0;
}
