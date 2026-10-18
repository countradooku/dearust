/* Raw Darwin queries exercise runtime wrappers, avoiding libc PID caching. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <unistd.h>
static uint64_t micros(void){struct timeval t;assert(gettimeofday(&t,NULL)==0);return (uint64_t)t.tv_sec*1000000+t.tv_usec;}
static uint64_t cpu(void){struct rusage r;assert(getrusage(RUSAGE_SELF,&r)==0);return ((uint64_t)r.ru_utime.tv_sec+r.ru_stime.tv_sec)*1000000+r.ru_utime.tv_usec+r.ru_stime.tv_usec;}
static uint64_t roundtrip(void){
    long pid=syscall(SYS_getpid),parent=syscall(SYS_getppid),group=syscall(SYS_getpgrp);
    long pgid=syscall(SYS_getpgid,0),session=syscall(SYS_getsid,0);
    assert(pid>0 && parent>0 && group>0 && pgid==group && session>0);
    return (uint64_t)pid+(uint64_t)parent+(uint64_t)group+(uint64_t)pgid+(uint64_t)session;
}
int main(void){
    alarm(60);
    pid_t pid=getpid(),parent=getppid(),group=getpgrp(),pgid=getpgid(0),session=getsid(0);
    assert(pid>0 && parent>0 && group>0 && pgid==group && session>0);
    assert(syscall(SYS_getpid)==pid && syscall(SYS_getppid)==parent && syscall(SYS_getpgrp)==group);
    assert(syscall(SYS_getpgid,0)==pgid && syscall(SYS_getsid,0)==session);
    uint64_t expected=(uint64_t)pid+(uint64_t)parent+(uint64_t)group+(uint64_t)pgid+(uint64_t)session;
    for(unsigned i=0;i<100;i++)assert(roundtrip()==expected);
    for(unsigned sample=0;sample<21;sample++){
        uint64_t checksum=0,c=cpu(),w=micros();
        for(unsigned i=0;i<10000;i++)checksum+=roundtrip();
        w=micros()-w;c=cpu()-c;assert(checksum==expected*10000);
        fprintf(stderr,"IDENTIFIERS_BENCH sample=%u roundtrips=10000 cpu_us=%llu wall_us=%llu\n",sample,(unsigned long long)c,(unsigned long long)w);
    }
    puts("Dearust guest identifiers benchmark checks passed");return 0;
}
