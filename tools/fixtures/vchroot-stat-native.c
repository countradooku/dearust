/* Compare the unchanged resolver with its private invocation-local stat bridge.
 * This is native Linux fixture coverage; actual guest chain gates remain required. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
struct vchroot_expand_args {char path[4096];unsigned flags;int dfd;};
extern int original_root(int),bridge_root(int);
extern int original_expand(struct vchroot_expand_args *),vchroot_expand_stat(struct vchroot_expand_args *,struct stat *);
static unsigned cases,snapshots,fallbacks;
static void check(int fd,const char *path,unsigned flags,int expect_snapshot){
    struct vchroot_expand_args original={.flags=flags,.dfd=fd},bridge=original;
    assert(strlen(path)<sizeof(original.path));strcpy(original.path,path);strcpy(bridge.path,path);
    struct {unsigned char head[16];struct stat value;unsigned char tail[16];} result;
    memset(&result,0xa5,sizeof(result));
    int a=original_expand(&original),b=vchroot_expand_stat(&bridge,&result.value);cases++;
    assert(a==(b<0?b:0));assert(strcmp(original.path,bridge.path)==0);
    for(unsigned i=0;i<16;i++)assert(result.head[i]==0xa5 && result.tail[i]==0xa5);
    if(b==0){
        assert(expect_snapshot!=0);snapshots++;
        struct stat reference;assert(lstat(bridge.path,&reference)==0 && !S_ISLNK(reference.st_mode));
        assert(memcmp(&result.value,&reference,sizeof(reference))==0);
        /* For a non-link, Linux follow/no-follow return the same metadata. */
        struct stat follow;assert(stat(bridge.path,&follow)==0 && memcmp(&follow,&reference,sizeof(follow))==0);
    }else {
        assert(expect_snapshot!=1);
        if(b==1)fallbacks++;
        /* Scratch metadata may be written then invalidated by recursion;
         * the status alone controls whether the caller may use it. */
    }
}
int main(void){
    char root[]="/tmp/dearust-vchroot-stat-XXXXXX";assert(mkdtemp(root));
    int fd=open(root,O_DIRECTORY|O_RDONLY);assert(fd>=0);
    assert(mkdirat(fd,"dir",0700)==0);
    int file=openat(fd,"file",O_CREAT|O_EXCL|O_WRONLY,0640);assert(file>=0 && write(file,"snapshot",8)==8 && close(file)==0);
    file=openat(fd,"dir/Inner",O_CREAT|O_EXCL|O_WRONLY,0600);assert(file>=0 && close(file)==0);
    assert(symlinkat("file",fd,"link")==0 && symlinkat("missing",fd,"broken")==0);
    assert(symlinkat("dir",fd,"dirlink")==0 && symlinkat("loop",fd,"loop")==0);
    assert(original_root(fd)==0 && bridge_root(fd)==0);
    for(unsigned follow=0;follow<2;follow++){
        check(fd,"/file",follow,1);check(fd,"file",follow,1);
        check(fd,"/dir/Inner",follow,1);check(fd,"/dirlink/Inner",follow,1);
        check(fd,"/dir/../file",follow,1);check(fd,"//dir///Inner",follow,1);
        check(fd,"/file/",follow,0);check(fd,"/dir/.",follow,0);
        check(fd,"/dir/..",follow,0);check(fd,"/",follow,0);
        check(fd,"/link",follow,0);check(fd,"/broken",follow,0);
        check(fd,"/dir/inner",follow,0);check(fd,"/missing",follow,0);
        check(fd,"/missing/sub",follow,0);check(fd,"/loop",follow,0);
        check(fd,"/Volumes/SystemRoot",follow,0);
        check(fd,"/Volumes/SystemRoot/proc/self/fd/0",follow,0);
        check(-42,"file",follow,0);check(fd,"",follow,0);
    }
    assert(snapshots==12 && fallbacks>0 && cases==40);
    assert(unlinkat(fd,"link",0)==0 && unlinkat(fd,"broken",0)==0 && unlinkat(fd,"dirlink",0)==0 && unlinkat(fd,"loop",0)==0);
    assert(unlinkat(fd,"dir/Inner",0)==0 && unlinkat(fd,"dir",AT_REMOVEDIR)==0 && unlinkat(fd,"file",0)==0);
    assert(close(fd)==0 && rmdir(root)==0);
    printf("vchroot snapshot: %u differential cases, %u snapshots, %u fallbacks; exact paths/status/metadata and output guards\n",cases,snapshots,fallbacks);
}
