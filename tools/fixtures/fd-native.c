/* Darling GPL-3.0-or-later ABI: exact arguments and real Linux fd operations. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/resource.h>
#include <limits.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
extern long sys_fchmod(int,int), sys_ftruncate(int,long long), sys_fsync(int), sys_fsync_nocancel(int), sys_fdatasync(int), sys_flock(int,int);
extern long long sys_lseek(int,long long,int);
extern long sys_umask(unsigned int), sys_getdtablesize(void);
struct xnu_rlimit { uint64_t current, maximum; long reserved[20]; };
_Static_assert(sizeof(struct xnu_rlimit)==176, "xnu_rlimit size");
_Static_assert(_Alignof(struct xnu_rlimit)==8, "xnu_rlimit alignment");
_Static_assert(__builtin_offsetof(struct xnu_rlimit,current)==0, "current offset");
_Static_assert(__builtin_offsetof(struct xnu_rlimit,maximum)==8, "maximum offset");
static int limit_injected, limit_calls;
static long limit_status;
static uint64_t limit_current;
long sys_getrlimit(unsigned int which, struct xnu_rlimit *limit) {
    assert(which==8 && ((uintptr_t)limit%8)==0); limit_calls++;
    unsigned char zero[176]={0}; assert(memcmp(limit,zero,sizeof(zero))==0);
    if(limit_injected) {
        memset(limit,0xa5,sizeof(*limit)); limit->current=limit_current;
        limit->maximum=UINT64_MAX; return limit_status;
    }
    struct rlimit real; assert(getrlimit(RLIMIT_NOFILE,&real)==0);
    limit->current=real.rlim_cur-1; limit->maximum=real.rlim_max-1; return 0;
}
static void limits(void) {
    limit_injected=1;
    const uint64_t values[]={0,1,INT_MAX-1,INT_MAX,(uint64_t)INT_MAX+1,UINT64_C(1)<<63,UINT64_MAX};
    for(unsigned i=0;i<sizeof(values)/sizeof(values[0]);i++) {
        limit_current=values[i]; limit_status=0;
        long expected=values[i]>INT_MAX?INT_MAX:(long)values[i];
        assert(sys_getdtablesize()==expected);
        limit_status=7;assert(sys_getdtablesize()==expected);
    }
    limit_status=-35; assert(sys_getdtablesize()==-35);
    limit_status=INT64_C(0x1ffffffdd); assert(sys_getdtablesize()==-35);
    limit_status=-INT64_C(0x100000023); assert(sys_getdtablesize()==-35);
    limit_status=INT64_MIN; limit_current=4; assert(sys_getdtablesize()==4);
    limit_status=INT64_C(0x100000000); limit_current=5; assert(sys_getdtablesize()==5);
    limit_injected=0;struct rlimit real;assert(getrlimit(RLIMIT_NOFILE,&real)==0);
    uint64_t expected=real.rlim_cur-1;
    assert(sys_getdtablesize()==(expected>INT_MAX?INT_MAX:(long)expected));
    assert(limit_calls==20);
}
static int injected, calls, expected_nr;
static long expected[6], raw_result;
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr) {
    calls++;
    if (injected) {
        long actual[6] = {a,b,c,d,e,f};
        assert(nr == expected_nr && memcmp(actual,expected,sizeof(actual)) == 0);
        return raw_result;
    }
    long result = syscall(nr,a,b,c,d,e,f);
    return result < 0 ? -errno : result;
}
static long invoke(int index) {
    switch(index) {
        case 0:return sys_fchmod(-42,-3);
        case 1:return sys_ftruncate(-42,INT64_MIN + 123);
        case 2:return sys_fsync(-42);
        case 3:return sys_fsync_nocancel(-42);
        case 4:return sys_fdatasync(-42);
        case 5:return sys_flock(-42,-7);
        case 6:return sys_lseek(-42,INT64_MIN + 456,-9);
        default:return sys_umask(UINT32_C(0x80000003));
    }
}
int main(void) {
    const int numbers[] = {91,77,74,74,75,73,8,95};
    injected = 1;
    for(int i=0;i<8;i++) {
        memset(expected,0,sizeof(expected)); expected_nr = numbers[i]; expected[0] = -42;
        if(i==0)expected[1]=-3;
        if(i==7)expected[0]=UINT32_C(0x80000003);
        if(i==1)expected[1]=INT64_MIN+123;
        if(i==5)expected[1]=-7;
        if(i==6){expected[1]=INT64_MIN+456;expected[2]=-9;}
        raw_result=INT64_C(0x100000007); assert(invoke(i)==(i==6 ? raw_result : 7));
        raw_result=INT64_C(0x1fffffff5); assert(invoke(i)==(i==6 ? raw_result : -35));
        raw_result=-11; assert(invoke(i)==-35);
        raw_result=-4; int before=calls; assert(invoke(i)==-4 && calls==before+1);
        raw_result=-INT64_C(0x10000000b); assert(invoke(i)==-35);
    }
    injected=0;
    mode_t original=umask(0);
    assert(sys_umask(027)==0);
    assert(sys_umask(UINT32_MAX)==027);
    assert(umask(original)==0777);
    limits();
    char name[]="/tmp/dearust-fd-XXXXXX"; int fd=mkstemp(name); assert(fd>=0);
    assert(write(fd,"kept",4)==4 && sys_lseek(fd,2,SEEK_SET)==2);
    assert(sys_fchmod(fd,0641)==0);
    struct stat st; assert(fstat(fd,&st)==0 && (st.st_mode&07777)==0641);
    long long large=(INT64_C(1)<<32)+123;
    assert(sys_ftruncate(fd,large)==0 && fstat(fd,&st)==0 && st.st_size==large);
    assert(sys_lseek(fd,0,SEEK_CUR)==2 && sys_lseek(fd,0,SEEK_END)==large);
    assert(sys_lseek(fd,-1,SEEK_END)==large-1);
    unsigned char data[8]; memset(data,0xa5,sizeof(data));
    assert(pread(fd,data+1,1,large-1)==1 && data[1]==0 && data[0]==0xa5 && data[2]==0xa5);
    assert(pread(fd,data+1,4,0)==4 && memcmp(data+1,"kept",4)==0 && data[0]==0xa5 && data[5]==0xa5);
    assert(sys_fsync(fd)==0 && sys_fsync_nocancel(fd)==0 && sys_fdatasync(fd)==0);
    int duplicate=dup(fd), independent=open(name,O_RDWR); assert(duplicate>=0 && independent>=0);
    assert(sys_lseek(fd,3,SEEK_SET)==3 && sys_lseek(duplicate,0,SEEK_CUR)==3 && sys_lseek(independent,0,SEEK_CUR)==0);
    assert(sys_flock(fd,LOCK_EX|LOCK_NB)==0);
    assert(sys_flock(duplicate,LOCK_EX|LOCK_NB)==0);
    assert(sys_flock(independent,LOCK_EX|LOCK_NB)==-35);
    assert(sys_flock(duplicate,LOCK_UN)==0 && sys_flock(independent,LOCK_SH|LOCK_NB)==0);
    assert(sys_flock(fd,LOCK_SH|LOCK_NB)==0 && sys_flock(fd,LOCK_UN)==0 && sys_flock(independent,LOCK_UN)==0);
    assert(sys_flock(fd,0)==-22 && sys_lseek(fd,0,-1)==-22 && sys_lseek(fd,-1,SEEK_SET)==-22);
    assert(sys_ftruncate(fd,-1)==-22 && sys_ftruncate(fd,INT64_MIN)==-22);
    assert(sys_ftruncate(fd,2)==0 && fstat(fd,&st)==0 && st.st_size==2 && sys_lseek(fd,0,SEEK_CUR)==3);
    assert(sys_fchmod(-42,0600)==-9 && sys_ftruncate(-42,0)==-9 && sys_fsync(-42)==-9 && sys_fsync_nocancel(-42)==-9 && sys_fdatasync(-42)==-9 && sys_lseek(-42,0,SEEK_SET)==-9 && sys_flock(-42,LOCK_UN)==-9);
    int pipefd[2]; assert(pipe(pipefd)==0 && sys_lseek(pipefd[0],0,SEEK_CUR)==-29);
    assert(close(pipefd[0])==0 && close(pipefd[1])==0);
    assert(close(duplicate)==0 && close(independent)==0 && close(fd)==0 && unlink(name)==0);
    puts("fd: nine ABI exports, exact widths, one-call EINTR, real sparse/seek/sync/mode/shared locks");
}
