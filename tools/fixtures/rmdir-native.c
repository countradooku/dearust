/* Original Darling ABI; explicit TLS/vchroot adapters and actual Linux calls. */
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
struct expanded {char path[4096];unsigned flags;int dfd;};
extern long sys_rmdir(const char *);
static int phase,cwd_fd,expansion_error,recording,calls;
static long raw_result;
int dearust_get_perthread_wd(void){assert(phase==0);phase=1;return cwd_fd;}
int vchroot_expand(struct expanded *args){assert(phase==1 && args->dfd==cwd_fd && args->flags==0);assert(memchr(args->path,0,4096));phase=2;return expansion_error;}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){
    long memory_result; if(dearust_native_memory_syscall(a,b,c,d,e,f,nr,&memory_result)) return memory_result;
    assert(phase==2 && nr==263 && a==cwd_fd && b && c==0x200 && d==0 && e==0 && f==0);phase=3;calls++;
    if(recording)return raw_result;
    long r=syscall(nr,a,b,c,d,e,f);return r<0?-errno:r;
}
static long run(const char *path){phase=0;return sys_rmdir(path);}
int main(void){
    char directory[]="/tmp/dearust-rmdir-native-XXXXXX";assert(mkdtemp(directory));
    cwd_fd=open(directory,O_RDONLY|O_DIRECTORY);assert(cwd_fd>=0);
    assert(mkdirat(cwd_fd,"victim",0700)==0 && run("victim")==0);
    assert(mkdirat(cwd_fd,"kept",0700)==0 && symlinkat("kept",cwd_fd,"link")==0);
    assert(run("link")==-20);struct stat st;assert(fstatat(cwd_fd,"kept",&st,0)==0 && S_ISDIR(st.st_mode));
    int fd=openat(cwd_fd,"kept/file",O_CREAT|O_WRONLY,0600);assert(fd>=0 && close(fd)==0);
    assert(run("kept")==-66 && run("missing")==-2);
    expansion_error=-11;int before=calls;assert(run("kept")==-35 && phase==2 && calls==before);expansion_error=0;
    assert(run(NULL)==-14 && phase==0);
    char path[4096];memset(path,'x',sizeof(path));assert(run(path)==-63 && phase==1 && calls==before);
    recording=1;path[4095]=0;raw_result=0;assert(run(path)==0);
    raw_result=INT64_C(0x100000007);assert(run("recorded")==0);
    raw_result=INT64_C(0x1fffffff5);assert(run("recorded")==-35);
    raw_result=-INT64_C(0x10000000b);assert(run("recorded")==-35);
    raw_result=-4;before=calls;assert(run("recorded")==-4 && calls==before+1);recording=0;
    assert(unlinkat(cwd_fd,"kept/file",0)==0 && unlinkat(cwd_fd,"link",0)==0 && run("kept")==0);
    assert(close(cwd_fd)==0 && rmdir(directory)==0);
    puts("rmdir: TLS/expansion ordering, nofollow, bounded paths, raw widths and real directories");
}
