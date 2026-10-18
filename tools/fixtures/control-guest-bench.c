/* Warm guest mutator/error paths; successful session creation is not timed. */
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <unistd.h>
static uint64_t micros(void){struct timeval t;assert(gettimeofday(&t,NULL)==0);return (uint64_t)t.tv_sec*1000000+t.tv_usec;}
static uint64_t cpu(void){struct rusage r;assert(getrusage(RUSAGE_SELF,&r)==0);return ((uint64_t)r.ru_utime.tv_sec+r.ru_stime.tv_sec)*1000000+r.ru_utime.tv_usec+r.ru_stime.tv_usec;}
static void roundtrip(long expected){
    assert(syscall(SYS_setpgid,0,0)==0);
    errno=0;assert(syscall(SYS_setsid)==-1 && errno==EPERM);
    assert(syscall(SYS_umask,022)==022);
    assert(syscall(SYS_getdtablesize)==expected);
}
int main(void){
    alarm(60);int channel[2];assert(pipe(channel)==0);
    pid_t parent=getpid(),group=getpgrp(),session=getsid(0);pid_t child=fork();assert(child>=0);
    if(child==0){
        assert(close(channel[0])==0);mode_t inherited=(mode_t)syscall(SYS_umask,022);
        assert(write(channel[1],&inherited,sizeof(inherited))==sizeof(inherited) && close(channel[1])==0);
        assert(syscall(SYS_setpgid,0,0)==0);
        /* libc getrlimit reports the retained Darling NOFILE-1 policy already. */
        struct rlimit limit;assert(getrlimit(RLIMIT_NOFILE,&limit)==0);
        uint64_t soft=(uint64_t)limit.rlim_cur;long expected=soft>INT_MAX?INT_MAX:(long)soft;
        for(unsigned i=0;i<100;i++)roundtrip(expected);
        for(unsigned sample=0;sample<21;sample++){
            uint64_t c=cpu(),w=micros();for(unsigned i=0;i<10000;i++)roundtrip(expected);w=micros()-w;c=cpu()-c;
            fprintf(stderr,"CONTROL_BENCH sample=%u roundtrips=10000 cpu_us=%llu wall_us=%llu\n",sample,(unsigned long long)c,(unsigned long long)w);
        }
        _exit(0);
    }
    assert(close(channel[1])==0);mode_t original,after;
    assert(read(channel[0],&original,sizeof(original))==sizeof(original) && close(channel[0])==0);
    int status;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
    assert(pipe(channel)==0);child=fork();assert(child>=0);
    if(child==0){close(channel[0]);mode_t mask=(mode_t)syscall(SYS_umask,022);assert(write(channel[1],&mask,sizeof(mask))==sizeof(mask));_exit(0);}
    assert(close(channel[1])==0 && read(channel[0],&after,sizeof(after))==sizeof(after) && close(channel[0])==0);
    assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
    assert(original==after && getpid()==parent && getpgrp()==group && getsid(0)==session);
    puts("Dearust guest control benchmark checks passed");return 0;
}
