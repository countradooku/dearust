/* Native metadata adapters preserve field/guard checks, not Darwin layout proof. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "guest-memory-native.h"
#include "darwin-stat-native.h"
struct expanded {char path[4096];unsigned flags;int dfd;};
struct output {unsigned char head[16];_Alignas(8) unsigned char value[144];int wide;unsigned char tail[16];};
extern long sys_stat(const char *,void *),sys_stat64(const char *,void *);
static int current_wd=AT_FDCWD,wd_calls,atfd_calls,expansions,conversions,expansion_error;
int dearust_get_perthread_wd(void){wd_calls++;return current_wd;}
int atfd(int fd){atfd_calls++;assert(fd==current_wd);return fd;}
int atflags_bsd_to_linux(int flags){assert(flags==0);return 0;}
int vchroot_expand(struct expanded *args){assert(args->dfd==current_wd && args->flags==1);assert(memchr(args->path,0,sizeof(args->path)));expansions++;return expansion_error;}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){
    long memory_result; if(dearust_native_memory_syscall(a,b,c,d,e,f,nr,&memory_result)) return memory_result;
assert(nr==SYS_newfstatat && d==0 && e==0 && f==0);long r=syscall(nr,a,b,c,d,e,f);return r<0?-errno:r;}
_Static_assert(sizeof(struct stat)==144,"Linux x86_64 stat ABI");
void __getuidgid(int32_t *uid,int32_t *gid){conversions++;*uid=(int32_t)getuid();*gid=(int32_t)getgid();}

int main(void){
    char root[]="/tmp/dearust-stat-XXXXXX";assert(mkdtemp(root));int dfd=open(root,O_RDONLY|O_DIRECTORY);assert(dfd>=0);
    int fd=openat(dfd,"file",O_CREAT|O_EXCL|O_RDWR,0640);assert(fd>=0 && write(fd,"payload",7)==7);
    assert(symlinkat("file",dfd,"link")==0 && symlinkat("missing",dfd,"dangling")==0);
    struct stat reference;assert(fstat(fd,&reference)==0);
    current_wd=dfd;
    for(int wide=0;wide<2;wide++){
        long (*call)(const char *,void *)=wide?sys_stat64:sys_stat;
        struct output out,before;memset(&out,0xa5,sizeof(out));before=out;
        int wd=wd_calls,at=atfd_calls,ex=expansions,cv=conversions;
        assert(call(NULL,&out.value)==-14 && wd_calls==wd+1 && atfd_calls==at);
        assert(call((const char *)(uintptr_t)1,NULL)==-14 && wd_calls==wd+2 && atfd_calls==at);
        char longpath[4096];memset(longpath,'x',sizeof(longpath));
        assert(call(longpath,&out.value)==-63 && wd_calls==wd+3 && expansions==ex && conversions==cv);
        assert(memcmp(&before,&out,sizeof(out))==0);
        expansion_error=-13;assert(call("file",&out.value)==-13 && conversions==cv);expansion_error=0;
        assert(memcmp(&before,&out,sizeof(out))==0);
        assert(call("link",&out.value)==0);
        assert(S_ISREG(native_view(out.value,wide).st_mode) && native_view(out.value,wide).st_ino==(wide?reference.st_ino:(uint32_t)reference.st_ino) && native_view(out.value,wide).st_dev==reference.st_dev && native_view(out.value,wide).st_size==7);
        assert(native_view(out.value,wide).st_uid==reference.st_uid && native_view(out.value,wide).st_gid==reference.st_gid && native_view(out.value,wide).st_nlink==reference.st_nlink);
        for(size_t i=0;i<sizeof(out.head);i++)assert(out.head[i]==0xa5 && out.tail[i]==0xa5);
        before=out;assert(call("missing",&out.value)==-2 && memcmp(&before,&out,sizeof(out))==0);
        assert(call("dangling",&out.value)==-2 && memcmp(&before,&out,sizeof(out))==0);
        current_wd=-42;assert(call("file",&out.value)==-9 && memcmp(&before,&out,sizeof(out))==0);current_wd=dfd;
        assert(call("file",&out.value)==0);
    }
    assert(close(fd)==0 && unlinkat(dfd,"link",0)==0 && unlinkat(dfd,"dangling",0)==0 && unlinkat(dfd,"file",0)==0);
    assert(close(dfd)==0 && rmdir(root)==0);
    puts("stat/stat64: TLS-before-validation, real fields, follow policy, errors and output guards");
}
