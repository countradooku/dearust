/* Root state commits only after descriptor/path validation and RPC success. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
struct vchroot_expand_args {char path[4096];unsigned flags;int dfd;};
extern int rpc_root(int),rpc_expand(struct vchroot_expand_args *);
static int rpc_calls,rpc_error;
int dserver_rpc_vchroot(int fd){assert(fd>=0);rpc_calls++;return rpc_error;}
static void check_prefix(const char *prefix){
    struct vchroot_expand_args args={.dfd=-100};strcpy(args.path,"/marker");
    assert(rpc_expand(&args)==0);char expected[4096];assert(snprintf(expected,sizeof(expected),"%s/marker",prefix)>0);
    assert(strcmp(args.path,expected)==0);
}
int main(void){
    char a[]="/tmp/dearust-root-a-XXXXXX",b[]="/tmp/dearust-root-b-XXXXXX";
    assert(mkdtemp(a)&&mkdtemp(b));int fa=open(a,O_RDONLY|O_DIRECTORY),fb=open(b,O_RDONLY|O_DIRECTORY);assert(fa>=0&&fb>=0);
    assert(rpc_root(fa)==0 && rpc_calls==1);check_prefix(a);
    assert(rpc_root(-42)==-EBADF && rpc_calls==1);check_prefix(a);
    int regular=openat(fa,"regular",O_CREAT|O_EXCL|O_RDONLY,0600);assert(regular>=0);
    assert(rpc_root(regular)==-ENOTDIR && rpc_calls==1);check_prefix(a);
    rpc_error=-EACCES;assert(rpc_root(fb)==-EACCES && rpc_calls==2);check_prefix(a);
    rpc_error=0;assert(rpc_root(fb)==0 && rpc_calls==3);check_prefix(b);
    assert(close(regular)==0 && unlinkat(fa,"regular",0)==0 && close(fa)==0 && close(fb)==0);
    assert(rmdir(a)==0&&rmdir(b)==0);puts("vchroot root transaction: validation/rejection preserve old prefix; successful RPC commits once");return 0;
}
