/* Native real directory/mode/access roundtrip; explicit boundary adapters. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
extern long sys_mkdirat(int,const char *,unsigned);
extern long sys_faccessat(int,const char *,int,int);
extern long sys_fchmodat(int,const char *,int,int);
struct expanded {char path[4096];unsigned flags;int dfd;};
int atfd(int fd){return fd==-2?-100:fd;}
int atflags_bsd_to_linux(int flags){assert(flags==0);return 0;}
int vchroot_expand(struct expanded *args){(void)args;return 0;}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){long r=syscall(nr,a,b,c,d,e,f);return r<0?-errno:r;}
static uint64_t nanos(clockid_t clock){struct timespec t;assert(clock_gettime(clock,&t)==0);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void roundtrip(int fd){assert(sys_mkdirat(fd,"entry",0700)==0);assert(sys_fchmodat(fd,"entry",0700,0)==0);assert(sys_faccessat(fd,"entry",R_OK|X_OK,0)==0);assert(unlinkat(fd,"entry",AT_REMOVEDIR)==0);}
int main(int argc,char **argv){
    assert(argc==2);unsigned n=(unsigned)strtoul(argv[1],NULL,10);assert(n>0);
    char path[]="/tmp/dearust-permissions-bench-XXXXXX";assert(mkdtemp(path));int fd=open(path,O_RDONLY|O_DIRECTORY);assert(fd>=0);
    for(unsigned i=0;i<100;i++)roundtrip(fd);
    uint64_t c=nanos(CLOCK_THREAD_CPUTIME_ID),w=nanos(CLOCK_MONOTONIC);
    for(unsigned i=0;i<n;i++)roundtrip(fd);
    w=nanos(CLOCK_MONOTONIC)-w;c=nanos(CLOCK_THREAD_CPUTIME_ID)-c;
    assert(close(fd)==0&&rmdir(path)==0);printf("%u %llu %llu\n",n,(unsigned long long)c,(unsigned long long)w);return 0;
}
