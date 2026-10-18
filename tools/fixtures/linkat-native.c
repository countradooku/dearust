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
struct expanded { char path[4096]; unsigned flags; int dfd; };
extern long sys_linkat(int, const char *, int, const char *, int);
static int cwd_fd, src_fd, dst_fd, follow, calls, fail_call, flag_calls;
int atfd(int fd) { return fd == -2 ? cwd_fd : fd; }
int vchroot_expand(struct expanded *p) {
    ++calls;
    assert(p->dfd == (calls % 2 ? src_fd : dst_fd));
    assert(p->flags == (calls % 2 ? (unsigned)follow : 0));
    assert(memchr(p->path, 0, sizeof(p->path)));
    return calls == fail_call ? -36 : 0;
}
int atflags_bsd_to_linux(int f) {
    ++flag_calls;
    assert(calls == 2);
    return (f & ~0x40) ? 1 : ((f & 0x40) ? AT_SYMLINK_FOLLOW : 0);
}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr) {
    assert(nr == SYS_linkat && f == 0);
    long r = syscall(nr,a,b,c,d,e,f); return r < 0 ? -errno : r;
}
static long run(const char *a,const char *b,int flags) {
    calls=0; follow=!!(flags&0x40); return sys_linkat(src_fd,a,dst_fd,b,flags);
}
static struct stat info(int fd,const char *p) { struct stat s; assert(fstatat(fd,p,&s,AT_SYMLINK_NOFOLLOW)==0);return s; }
int main(void) {
    char temp[]="/tmp/dearust-linkat-XXXXXX";assert(mkdtemp(temp));
    int base=open(temp,O_RDONLY|O_DIRECTORY);assert(base>=0);
    assert(mkdirat(base,"src",0700)==0 && mkdirat(base,"dst",0700)==0);
    src_fd=openat(base,"src",O_RDONLY|O_DIRECTORY);dst_fd=openat(base,"dst",O_RDONLY|O_DIRECTORY);
    assert(src_fd>=0 && dst_fd>=0);
    int fd=openat(src_fd,"file",O_CREAT|O_WRONLY,0600);assert(fd>=0);close(fd);
    assert(symlinkat("file",src_fd,"sym")==0);
    assert(run("file","hard",0)==0);assert(info(src_fd,"file").st_ino==info(dst_fd,"hard").st_ino);
    assert(run("sym","nofollow",0)==0);assert(S_ISLNK(info(dst_fd,"nofollow").st_mode));
    assert(run("sym","follow",0x40)==0);assert(info(dst_fd,"follow").st_ino==info(src_fd,"file").st_ino);
    assert(run("file","hard",0)==-17);assert(run("missing","none",0)==-2);
    assert(run("file","invalid",0x80000000)==-22);
    fail_call=1;flag_calls=0;assert(run("file","invalid",0x80000000)==-63);assert(calls==1 && flag_calls==0);
    fail_call=2;assert(run("file","invalid",0)==-63);assert(calls==2 && flag_calls==0);fail_call=0;
    calls=0;assert(sys_linkat(src_fd,NULL,dst_fd,"none",0)==-14);assert(sys_linkat(src_fd,"file",dst_fd,NULL,0)==-14);
    char longpath[4096];memset(longpath,'x',sizeof(longpath));
    assert(sys_linkat(src_fd,longpath,dst_fd,"none",0)==-63);assert(sys_linkat(src_fd,"file",dst_fd,longpath,0)==-63);assert(calls==0);
    cwd_fd=src_fd;calls=0;assert(sys_linkat(-2,"file",dst_fd,"thread",0)==0);
    src_fd=-42;assert(run("file","badfd",0)==-9);src_fd=cwd_fd;
    const char *names[]={"hard","nofollow","follow","thread"};for(unsigned i=0;i<4;i++)assert(unlinkat(dst_fd,names[i],0)==0);
    assert(unlinkat(src_fd,"sym",0)==0 && unlinkat(src_fd,"file",0)==0);
    close(src_fd);close(dst_fd);assert(unlinkat(base,"src",AT_REMOVEDIR)==0 && unlinkat(base,"dst",AT_REMOVEDIR)==0);close(base);assert(rmdir(temp)==0);
    puts("linkat: real hardlink identity, symlink policy, cwd, bounded input, flags and error ordering");
}
