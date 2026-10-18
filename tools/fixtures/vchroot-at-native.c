/* Native-only nofollow single-component resolver tests. Actual fd-relative
 * syscalls are used; fallback is a sentinel to prove all other paths delegate. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#define TEST
#define LINUX_ENOENT ENOENT
#define LINUX_SYSCALL(number,...) probe(number,__VA_ARGS__)
typedef struct stat stat_t;
struct vchroot_expand_args {char path[4096];unsigned flags;int dfd;};
static unsigned fallbacks,probes;
static int injected_errno;
static int vchroot_expand(struct vchroot_expand_args *args){(void)args;fallbacks++;return -777;}
static int probe(long nr,int fd,const char *name,struct stat *out,int flags){
    assert(nr==SYS_newfstatat && flags==AT_SYMLINK_NOFOLLOW);probes++;
    if(injected_errno)return -injected_errno;
    long rv=syscall(nr,fd,name,out,flags);return rv<0?-errno:(int)rv;
}
#include "vchroot-at-bridge.h"
static void check(int fd,const char *path,unsigned flags,int expected,unsigned expected_probes){
    struct {unsigned char before[16];struct vchroot_expand_args args;unsigned char after[16];} guarded;
    memset(&guarded,0xa5,sizeof(guarded));guarded.args.dfd=fd;guarded.args.flags=flags;
    assert(strlen(path)<sizeof(guarded.args.path));strcpy(guarded.args.path,path);
    unsigned old_probes=probes,old_fallbacks=fallbacks;
    assert(vchroot_expand_at(&guarded.args)==expected);
    assert(probes-old_probes==expected_probes);
    assert(fallbacks-old_fallbacks==(expected==-777));
    assert(strcmp(guarded.args.path,path)==0 && guarded.args.dfd==fd && guarded.args.flags==flags);
    for(unsigned i=0;i<16;i++)assert(guarded.before[i]==0xa5 && guarded.after[i]==0xa5);
}
int main(void){
    char root[]="/tmp/dearust-vchroot-at-XXXXXX";assert(mkdtemp(root));int fd=open(root,O_RDONLY|O_DIRECTORY);assert(fd>=0);
    int file=openat(fd,"Exact",O_CREAT|O_EXCL|O_RDWR,0600);assert(file>=0);
    assert(mkdirat(fd,"directory",0700)==0 && symlinkat("/outside-virtual-root",fd,"link")==0);
    off_t offset=lseek(fd,0,SEEK_CUR);assert(offset>=0);
    check(fd,"Exact",0,0,1);check(fd,"directory",0,0,1);check(fd,"link",0,0,1);
    check(fd,"exact",0,-777,1);check(fd,"missing",0,-777,1);
    check(-1,"Exact",0,-777,0);check(file,"child",0,-777,1);
    check(fd,"Exact",1,-777,0);check(fd,"Exact",2,-777,0);
    check(fd,"",0,-777,0);check(fd,".",0,-777,0);check(fd,"..",0,-777,0);
    check(fd,"directory/child",0,-777,0);check(fd,"/Exact",0,-777,0);
    check(fd,"SystemRoot",0,-777,0);check(fd,"sYsTeMrOoT",0,-777,0);
    for(int error=1;error<=133;error++){injected_errno=error;check(fd,"Exact",0,-777,1);}injected_errno=0;
    struct vchroot_expand_args overlong={.flags=0,.dfd=fd};memset(overlong.path,'x',sizeof(overlong.path));
    unsigned old=probes;assert(vchroot_expand_at(&overlong)==-777 && probes==old);
    assert(lseek(fd,0,SEEK_CUR)==offset);
    /* Exact broken symlinks stay un-followed through the real final operation. */
    struct vchroot_expand_args relative={.flags=0,.dfd=fd};strcpy(relative.path,"link");
    assert(vchroot_expand_at(&relative)==0 && unlinkat(relative.dfd,relative.path,0)==0);
    strcpy(relative.path,"directory");assert(vchroot_expand_at(&relative)==0 && unlinkat(relative.dfd,relative.path,AT_REMOVEDIR)==0);
    assert(close(file)==0 && unlinkat(fd,"Exact",0)==0 && close(fd)==0 && rmdir(root)==0);
    puts("vchroot at: exact nofollow names/symlinks, casefold fallback, flags, bounds, all error fallback, unchanged directory offset and guarded arguments passed");
}
