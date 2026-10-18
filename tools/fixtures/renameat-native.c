/* Real Linux calls with explicit guest TLS/vchroot boundary adapters. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "guest-memory-native.h"
struct expanded { char path[4096];unsigned flags;int dfd; };
extern long sys_renameat(int,const char *,int,const char *);
static int cwd_fd,src_fd,dst_fd,calls,fail_call,forced_positive;
int atfd(int fd) { return fd == -2 ? cwd_fd : fd; }
int vchroot_expand(struct expanded *p) {
    ++calls;assert(p->dfd==(calls%2?src_fd:dst_fd));assert(p->flags==0);
    assert(memchr(p->path,0,sizeof(p->path)));return calls==fail_call?-36:0;
}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr) {
    long memory_result; if(dearust_native_memory_syscall(a,b,c,d,e,f,nr,&memory_result)) return memory_result;
    assert(nr==SYS_renameat && e==0 && f==0);
    if(forced_positive)return 7;
    long r=syscall(nr,a,b,c,d,e,f);return r<0?-errno:r;
}
static long run(const char *a,const char *b) { calls=0;return sys_renameat(src_fd,a,dst_fd,b); }
static struct stat info(int fd,const char *p) { struct stat s;assert(fstatat(fd,p,&s,AT_SYMLINK_NOFOLLOW)==0);return s; }
int main(void) {
    char temp[]="/tmp/dearust-renameat-XXXXXX";assert(mkdtemp(temp));int base=open(temp,O_RDONLY|O_DIRECTORY);assert(base>=0);
    assert(mkdirat(base,"src",0700)==0 && mkdirat(base,"dst",0700)==0);
    src_fd=openat(base,"src",O_RDONLY|O_DIRECTORY);dst_fd=openat(base,"dst",O_RDONLY|O_DIRECTORY);assert(src_fd>=0&&dst_fd>=0);
    int fd=openat(src_fd,"file",O_CREAT|O_WRONLY,0600);assert(fd>=0);close(fd);
    fd=openat(dst_fd,"replace",O_CREAT|O_WRONLY,0600);assert(fd>=0);close(fd);
    ino_t ino=info(src_fd,"file").st_ino;assert(run("file","replace")==0);assert(info(dst_fd,"replace").st_ino==ino);
    assert(symlinkat("missing",src_fd,"sym")==0);ino=info(src_fd,"sym").st_ino;
    assert(run("sym","sym")==0);assert(S_ISLNK(info(dst_fd,"sym").st_mode)&&info(dst_fd,"sym").st_ino==ino);
    assert(run("missing","none")==-2);fail_call=1;assert(run("missing","none")==-63);assert(calls==1);
    fail_call=2;assert(run("missing","none")==-63);assert(calls==2);fail_call=0;
    calls=0;assert(sys_renameat(src_fd,NULL,dst_fd,"none")==-14);assert(sys_renameat(src_fd,"none",dst_fd,NULL)==-14);
    char longpath[4096];memset(longpath,'x',sizeof(longpath));assert(sys_renameat(src_fd,longpath,dst_fd,"none")==-63);
    assert(sys_renameat(src_fd,"none",dst_fd,longpath)==-63);assert(calls==0);
    assert(symlinkat("missing",src_fd,"cwd")==0);cwd_fd=src_fd;calls=0;assert(sys_renameat(-2,"cwd",dst_fd,"thread")==0);
    src_fd=-42;assert(run("file","badfd")==-9);src_fd=cwd_fd;
    forced_positive=1;assert(run("ignored","ignored")==0);forced_positive=0;
    assert(unlinkat(dst_fd,"replace",0)==0 && unlinkat(dst_fd,"sym",0)==0 && unlinkat(dst_fd,"thread",0)==0);
    close(src_fd);close(dst_fd);assert(unlinkat(base,"src",AT_REMOVEDIR)==0 && unlinkat(base,"dst",AT_REMOVEDIR)==0);close(base);assert(rmdir(temp)==0);
    puts("renameat: real replacement/identity, symlink, cwd, bounded input, errors and return policy");
}
