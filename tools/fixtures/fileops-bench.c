/* Native real filesystem roundtrips; virtual-root/TLS remain explicit adapters. */
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
extern long sys_unlinkat(int,const char *,int);
extern long sys_linkat(int,const char *,int,const char *,int);
extern long sys_renameat(int,const char *,int,const char *);
struct expanded {char path[4096]; unsigned flags; int dfd;};
int atfd(int fd) {return fd==-2?-100:fd;}
int atflags_bsd_to_linux(int flags) {assert(flags==0);return 0;}
int vchroot_expand(struct expanded *args) {(void)args;return 0;}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr) {long r=syscall(nr,a,b,c,d,e,f);return r<0?-errno:r;}
static uint64_t nanos(clockid_t clock) {struct timespec t;assert(clock_gettime(clock,&t)==0);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void roundtrip(int source,int destination) {
    assert(sys_linkat(source,"source",destination,"hard",0)==0);
    assert(sys_renameat(destination,"hard",source,"moved")==0);
    assert(sys_unlinkat(source,"moved",0)==0);
}
int main(int argc,char **argv) {
    assert(argc==2);unsigned iterations=(unsigned)strtoul(argv[1],NULL,10);assert(iterations>0);
    char directory[]="/tmp/dearust-fileops-bench-XXXXXX";assert(mkdtemp(directory));
    int source=open(directory,O_RDONLY|O_DIRECTORY);assert(source>=0);
    assert(mkdirat(source,"dest",0700)==0);int destination=openat(source,"dest",O_RDONLY|O_DIRECTORY);assert(destination>=0);
    int fd=openat(source,"source",O_CREAT|O_EXCL|O_WRONLY,0600);assert(fd>=0&&close(fd)==0);
    for(unsigned i=0;i<100;i++)roundtrip(source,destination);
    uint64_t cpu=nanos(CLOCK_THREAD_CPUTIME_ID),wall=nanos(CLOCK_MONOTONIC);
    for(unsigned i=0;i<iterations;i++)roundtrip(source,destination);
    wall=nanos(CLOCK_MONOTONIC)-wall;cpu=nanos(CLOCK_THREAD_CPUTIME_ID)-cpu;
    struct stat st;assert(fstatat(source,"source",&st,0)==0&&st.st_nlink==1);
    assert(unlinkat(source,"source",0)==0&&close(destination)==0&&unlinkat(source,"dest",AT_REMOVEDIR)==0&&close(source)==0&&rmdir(directory)==0);
    printf("%u %llu %llu\n",iterations,(unsigned long long)cpu,(unsigned long long)wall);return 0;
}
