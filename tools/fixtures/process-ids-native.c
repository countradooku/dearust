/* Darling GPL-3.0-or-later ABI with recorded arguments and real Linux syscalls. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>
extern long sys_getpid(void),sys_getppid(void),sys_getpgrp(void),sys_getpgid(int),sys_getsid(int);
static int injected,calls,expected_nr;
static long expected_pid,raw_result;
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr) {
    calls++;
    if(injected) {
        assert(nr==expected_nr && a==expected_pid && b==0 && c==0 && d==0 && e==0 && f==0);
        return raw_result;
    }
    long result=syscall(nr,a,b,c,d,e,f);
    return result<0 ? -errno : result;
}
static long invoke(int index) {
    switch(index) {
        case 0:return sys_getpid(); case 1:return sys_getppid(); case 2:return sys_getpgrp();
        case 3:return sys_getpgid(INT_MIN); default:return sys_getsid(INT_MIN);
    }
}
int main(void) {
    int numbers[]={39,110,111,121,124}; injected=1;
    for(int i=0;i<5;i++) {
        expected_nr=numbers[i];expected_pid=i<3?0:INT_MIN;
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
    puts("process ids: five exports, signed PIDs, C narrowing, parent-zero mapping and one-call EINTR");
}
