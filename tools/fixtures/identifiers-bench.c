/* Real native process queries; no files or allocations in the timed workload. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
extern long sys_getpid(void);
extern long sys_getppid(void);
extern long sys_getpgrp(void);
extern long sys_getpgid(int);
extern long sys_getsid(int);
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){long r=syscall(nr,a,b,c,d,e,f);return r<0?-errno:r;}
static uint64_t nanos(clockid_t clock){struct timespec t;assert(clock_gettime(clock,&t)==0);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static uint64_t roundtrip(void){
    long pid=sys_getpid(),parent=sys_getppid(),group=sys_getpgrp();
    long pgid=sys_getpgid(0),session=sys_getsid(0);
    assert(pid>0 && parent>0 && group>0 && pgid==group && session>0);
    return (uint64_t)pid+(uint64_t)parent+(uint64_t)group+(uint64_t)pgid+(uint64_t)session;
}
int main(int argc,char **argv){
    assert(argc==2);unsigned n=(unsigned)strtoul(argv[1],NULL,10);assert(n>0);
    pid_t pid=getpid(),parent=getppid(),group=getpgrp(),pgid=getpgid(0),session=getsid(0);
    assert(pid>0 && parent>=0 && group>0 && pgid==group && session>0);
    /* Darling intentionally maps a namespace-init PPID0 to1. */
    if(parent==0)parent=1;
    assert(sys_getpid()==pid && sys_getppid()==parent && sys_getpgrp()==group);
    assert(sys_getpgid(0)==pgid && sys_getsid(0)==session);
    uint64_t expected=(uint64_t)pid+(uint64_t)parent+(uint64_t)group+(uint64_t)pgid+(uint64_t)session;
    for(unsigned i=0;i<100;i++)assert(roundtrip()==expected);
    uint64_t checksum=0,c=nanos(CLOCK_THREAD_CPUTIME_ID),w=nanos(CLOCK_MONOTONIC);
    for(unsigned i=0;i<n;i++)checksum+=roundtrip();
    w=nanos(CLOCK_MONOTONIC)-w;c=nanos(CLOCK_THREAD_CPUTIME_ID)-c;
    assert(checksum==expected*n);
    printf("%u %llu %llu\n",n,(unsigned long long)c,(unsigned long long)w);return 0;
}
