/* Native explicit boundaries: real Linux files/syscalls, unchanged C algorithms. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/statfs.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
struct darwin_fs {
    uint32_t bsize;int32_t iosize;uint64_t blocks,bfree,bavail,files,ffree,fsid;
    uint32_t owner,type,flags,subtype;char typename[16],mountpoint[1024],source[1024];
    uint32_t flags_ext,reserved[7];
};
_Static_assert(sizeof(struct darwin_fs)==2168,"Darwin x64 statfs2168");
struct expanded{char path[4096];unsigned flags;int dfd;};
extern long sys_statfs(const char*,void*),sys_statfs64(const char*,void*);
extern long sys_fstatfs(int,void*),sys_fstatfs64(int,void*);
extern int errno_linux_to_bsd(int);
int dearust_get_perthread_wd(void){return AT_FDCWD;}
int vchroot_expand(struct expanded *args){assert(args->dfd==AT_FDCWD && args->path[0]=='/' && args->flags<=1);return 0;}
int vchroot_unexpand(void *args){assert(*(char*)args=='/');return 0;}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){long result=syscall(nr,a,b,c,d,e,f);return result<0?-errno:result;}
/* Explicit native replacements for the legacy runtime's filesystem boundaries.
 * The actual guest benchmark retains the original complete helper chain. */
long sys_open(const char *path,int flags,int mode){long result=linux_syscall(AT_FDCWD,(long)path,flags,mode,0,0,SYS_openat);return result<0?errno_linux_to_bsd((int)result):result;}
long close_internal(int fd){long result=linux_syscall(fd,0,0,0,0,0,SYS_close);return result<0?errno_linux_to_bsd((int)result):result;}
long sys_read(int fd,void *output,size_t length){long result=linux_syscall(fd,(long)output,(long)length,0,0,0,SYS_read);return result<0?errno_linux_to_bsd((int)result):result;}
int fdpath(int fd,char *output,size_t capacity){
    char path[64];int size=snprintf(path,sizeof(path),"/proc/self/fd/%d",fd);assert(size>0 && (size_t)size<sizeof(path));
    if(capacity==0)return -63;
    long result=linux_syscall(AT_FDCWD,(long)path,(long)output,(long)(capacity-1),0,0,SYS_readlinkat);
    if(result<0)return errno_linux_to_bsd((int)result);
    if((size_t)result>=capacity-1)return -63;
    output[result]=0;return 0;
}
static uint64_t nanos(clockid_t clock){struct timespec t;assert(clock_gettime(clock,&t)==0);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void roundtrip(const char *path,int fd,const struct statfs *reference){
    struct {uint64_t before;struct darwin_fs fs;uint64_t after;} output;
    output.before=output.after=UINT64_C(0xa5a5a5a5a5a5a5a5);
    /* Legacy C leaves several fields uninitialized and mislabels mount flags.
     * Compare only initialized, semantically common fields in timing checks. */
    assert(sys_statfs(path,&output.fs)==0);
    assert(output.fs.bsize==(uint32_t)reference->f_bsize && output.fs.blocks==reference->f_blocks);
    uint64_t fsid=output.fs.fsid;
    assert(sys_statfs64(path,&output.fs)==0 && output.fs.fsid==fsid);
    assert(sys_fstatfs(fd,&output.fs)==0 && output.fs.fsid==fsid);
    assert(sys_fstatfs64(fd,&output.fs)==0 && output.fs.fsid==fsid);
    assert(output.before==UINT64_C(0xa5a5a5a5a5a5a5a5) && output.after==UINT64_C(0xa5a5a5a5a5a5a5a5));
}
int main(int argc,char **argv){
    assert(argc==2);unsigned rounds=(unsigned)strtoul(argv[1],NULL,10);assert(rounds>0);alarm(120);
    char path[]="/tmp/dearust-filesystem-bench-XXXXXX";int fd=mkstemp(path);assert(fd>=0);
    struct statfs reference;assert(fstatfs(fd,&reference)==0);
    for(unsigned i=0;i<100;i++)roundtrip(path,fd,&reference);
    uint64_t cpu=nanos(CLOCK_THREAD_CPUTIME_ID),wall=nanos(CLOCK_MONOTONIC);
    for(unsigned i=0;i<rounds;i++)roundtrip(path,fd,&reference);
    wall=nanos(CLOCK_MONOTONIC)-wall;cpu=nanos(CLOCK_THREAD_CPUTIME_ID)-cpu;
    assert(close(fd)==0 && unlink(path)==0);
    printf("%u %llu %llu\n",rounds,(unsigned long long)cpu,(unsigned long long)wall);return 0;
}
