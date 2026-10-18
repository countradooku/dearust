/* Synthetic boundary costs and one real Linux fstatat workload. No guest claims. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
struct expanded {char path[4096]; unsigned flags; int dfd;};
extern long sys_lstat(const char *,void *);
extern long sys_fstatat(int,const char *,void *,int);
extern long sys_symlinkat(const char *,int,const char *);
static int real_calls;
int dearust_get_perthread_wd(void) {return -100;}
int atfd(int fd) {return fd==-2?-100:fd;}
int atflags_bsd_to_linux(int flags) {return flags;}
int vchroot_expand(struct expanded *args) {(void)args;return 0;}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr) {
    if(!real_calls)return 0;
    long result=syscall(nr,a,b,c,d,e,f);
    return result<0?-errno:result;
}
void stat_linux_to_bsd(const void *input,void *output) {memcpy(output,input,8);}
void stat_linux_to_bsd64(const void *input,void *output) {stat_linux_to_bsd(input,output);}
static uint64_t nanos(clockid_t clock) {struct timespec t;assert(clock_gettime(clock,&t)==0);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void run(const char *name,int kind,const char *path,size_t length,unsigned iterations) {
    uint64_t output=0;
    for(unsigned warm=0;warm<100;warm++) {
        long result=kind==0?sys_lstat(path,&output):kind==1?sys_fstatat(-2,path,&output,0):sys_symlinkat("payload",-2,path);
        assert(result==0);
    }
    uint64_t cpu=nanos(CLOCK_THREAD_CPUTIME_ID),wall=nanos(CLOCK_MONOTONIC);
    for(unsigned i=0;i<iterations;i++) {
        long result=kind==0?sys_lstat(path,&output):kind==1?sys_fstatat(-2,path,&output,0):sys_symlinkat("payload",-2,path);
        assert(result==0);
    }
    wall=nanos(CLOCK_MONOTONIC)-wall;cpu=nanos(CLOCK_THREAD_CPUTIME_ID)-cpu;
    printf("%s %zu %u %llu %llu\n",name,length,iterations,(unsigned long long)cpu,(unsigned long long)wall);
}
int main(int argc,char **argv) {
    assert(argc==2);unsigned iterations=(unsigned)strtoul(argv[1],NULL,10);assert(iterations>0);
    char path[4096];const size_t lengths[]={8,128,1024,4095};
    for(unsigned i=0;i<4;i++) {
        memset(path,'a',lengths[i]);path[0]='/';path[lengths[i]]=0;
        run("stub-lstat",0,path,lengths[i],iterations);
        run("stub-fstatat",1,path,lengths[i],iterations);
        run("stub-symlinkat",2,path,lengths[i],iterations);
    }
    char actual[]="/tmp/dearust-path-bench-XXXXXX";int fd=mkstemp(actual);assert(fd>=0&&close(fd)==0);
    real_calls=1;run("linux-fstatat",1,actual,strlen(actual),iterations);assert(unlink(actual)==0);
    return 0;
}
