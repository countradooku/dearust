/* Owned absolute-path filesystem roundtrip; explicit vchroot/TLS adapters. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
extern long sys_mkdirat(int,const char *,unsigned);
extern long sys_rmdir(const char *);
extern long sys_truncate(const char *,long long);
struct expanded {char path[4096];unsigned flags;int dfd;};
int atfd(int fd){return fd==-2?AT_FDCWD:fd;}
int atflags_bsd_to_linux(int flags){assert(flags==0);return 0;}
int get_perthread_wd(void){return AT_FDCWD;}
int dearust_get_perthread_wd(void){return AT_FDCWD;}
int vchroot_expand(struct expanded *args){(void)args;return 0;}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){long r=syscall(nr,a,b,c,d,e,f);return r<0?-errno:r;}
static uint64_t nanos(clockid_t clock){struct timespec t;assert(clock_gettime(clock,&t)==0);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void roundtrip(const char *directory,const char *file){assert(sys_mkdirat(-2,directory,0700)==0);assert(sys_rmdir(directory)==0);assert(sys_truncate(file,128)==0);}
int main(int argc,char **argv){
    assert(argc==2);unsigned n=(unsigned)strtoul(argv[1],NULL,10);assert(n>0);
    char root[]="/tmp/dearust-paths-bench-XXXXXX";assert(mkdtemp(root));
    char directory[256],file[256];assert(snprintf(directory,sizeof(directory),"%s/entry",root)>0 && snprintf(file,sizeof(file),"%s/file",root)>0);
    int fd=open(file,O_CREAT|O_EXCL|O_RDWR,0600);assert(fd>=0 && write(fd,"kept",4)==4 && lseek(fd,2,SEEK_SET)==2);
    for(unsigned i=0;i<100;i++)roundtrip(directory,file);
    uint64_t c=nanos(CLOCK_THREAD_CPUTIME_ID),w=nanos(CLOCK_MONOTONIC);
    for(unsigned i=0;i<n;i++)roundtrip(directory,file);
    w=nanos(CLOCK_MONOTONIC)-w;c=nanos(CLOCK_THREAD_CPUTIME_ID)-c;
    struct stat st;assert(fstat(fd,&st)==0 && st.st_size==128 && lseek(fd,0,SEEK_CUR)==2);
    char data[4];assert(pread(fd,data,sizeof(data),0)==sizeof(data) && memcmp(data,"kept",4)==0);
    errno=0;assert(lstat(directory,&st)==-1 && errno==ENOENT);
    assert(close(fd)==0 && unlink(file)==0 && rmdir(root)==0);
    printf("%u %llu %llu\n",n,(unsigned long long)c,(unsigned long long)w);return 0;
}
