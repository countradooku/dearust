/* Darling GPL-3.0-or-later ABI with recorded arguments and real Linux syscalls. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
extern long sys_getpid(void),sys_getppid(void),sys_getpgrp(void),sys_getpgid(int),sys_getsid(int),sys_setpgid(int,int),sys_setsid(void);
static int injected,calls,expected_nr;
static long expected_pid,expected_group,raw_result;
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr) {
    calls++;
    if(injected) {
        assert(nr==expected_nr && a==expected_pid && b==expected_group && c==0 && d==0 && e==0 && f==0);
        return raw_result;
    }
    long result=syscall(nr,a,b,c,d,e,f);
    return result<0 ? -errno : result;
}
static long invoke(int index) {
    switch(index) {
        case 0:return sys_getpid(); case 1:return sys_getppid(); case 2:return sys_getpgrp();
        case 3:return sys_getpgid(INT_MIN); case 4:return sys_getsid(INT_MIN);
        case 5:return sys_setpgid(INT_MIN,INT_MIN+1);default:return sys_setsid();
    }
}
int main(void) {
    int numbers[]={39,110,111,121,124,109,112}; injected=1;
    for(int i=0;i<7;i++) {
        expected_nr=numbers[i];expected_pid=(i<3 || i==6)?0:INT_MIN;expected_group=i==5?INT_MIN+1:0;
        raw_result=INT64_C(0x100000007);assert(invoke(i)==7);
        raw_result=INT64_C(0x1fffffff5);assert(invoke(i)==-35);
        raw_result=-11;assert(invoke(i)==-35);
        raw_result=-4;int before=calls;assert(invoke(i)==-4 && calls==before+1);
        raw_result=0;assert(invoke(i)==(i==1?1:0));
        raw_result=INT64_C(0x100000000);assert(invoke(i)==(i==1?1:0));
        /* C narrowing produces zero, then getppid performs zero->one. */
        raw_result=-INT64_C(0x100000000);assert(invoke(i)==(i==1?1:0));
    }
    injected=0;
    assert(sys_getpid()==getpid() && sys_getpid()==getpid());
    assert(sys_getppid()==(getppid()==0?1:getppid()));
    assert(sys_getpgrp()==getpgrp() && sys_getpgid(0)==getpgrp());
    assert(sys_getpgid(getpid())==getpgid(getpid()));
    assert(sys_getsid(0)==getsid(0) && sys_getsid(getpid())==getsid(getpid()));
    assert(sys_getpgid(-1)==-3 && sys_getsid(-1)==-3);
    pid_t parent=getpid(),group=getpgrp(),session=getsid(0);
    for(int session_child=0;session_child<2;session_child++) {
        pid_t child=fork();assert(child>=0);
        if(child==0) {
            pid_t self=getpid();assert(sys_getppid()==parent);
            assert(sys_setpgid(0,-1)==-22);
            if(session_child) {
                assert(sys_setsid()==self && sys_getsid(0)==self && sys_getpgid(0)==self);
                assert(sys_setsid()==-1 && sys_setpgid(0,0)==-1);
            } else {
                assert(sys_setpgid(0,0)==0 && sys_getpgid(0)==self && sys_getsid(0)==session);
                assert(sys_setpgid(self,self)==0 && sys_setsid()==-1);
            }
            _exit(0);
        }
        int status;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
    }
    assert(getpid()==parent && getpgrp()==group && getsid(0)==session);
    puts("process ids: seven exports, signed IDs, narrowing, parent-zero, one-call EINTR and child controls");
}
