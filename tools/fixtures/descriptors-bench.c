/* Actual native descriptor operations; scratch file, no application claims. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
extern long sys_fchmod(int,int);
extern long sys_ftruncate(int,long long);
extern long sys_fsync(int);
extern long sys_fdatasync(int);
extern long long sys_lseek(int,long long,int);
extern long sys_flock(int,int);
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){long r=syscall(nr,a,b,c,d,e,f);return r<0?-errno:r;}
static uint64_t nanos(clockid_t clock){struct timespec t;assert(clock_gettime(clock,&t)==0);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void roundtrip(int fd){assert(sys_fchmod(fd,0600)==0);assert(sys_ftruncate(fd,128)==0);assert(sys_lseek(fd,64,SEEK_SET)==64);assert(sys_flock(fd,LOCK_EX|LOCK_NB)==0);assert(sys_flock(fd,LOCK_UN)==0);assert(sys_fsync(fd)==0);assert(sys_fdatasync(fd)==0);}
int main(int argc,char **argv){
    assert(argc==2);unsigned n=(unsigned)strtoul(argv[1],NULL,10);assert(n>0);
    char path[]="/tmp/dearust-descriptors-bench-XXXXXX";int fd=mkstemp(path);assert(fd>=0);assert(unlink(path)==0);
    for(unsigned i=0;i<100;i++)roundtrip(fd);
    uint64_t c=nanos(CLOCK_THREAD_CPUTIME_ID),w=nanos(CLOCK_MONOTONIC);
    for(unsigned i=0;i<n;i++)roundtrip(fd);
    w=nanos(CLOCK_MONOTONIC)-w;c=nanos(CLOCK_THREAD_CPUTIME_ID)-c;
    struct stat st;assert(fstat(fd,&st)==0 && st.st_size==128 && (st.st_mode&0777)==0600);
    assert(lseek(fd,0,SEEK_CUR)==64);
    assert(close(fd)==0);printf("%u %llu %llu\n",n,(unsigned long long)c,(unsigned long long)w);return 0;
}
