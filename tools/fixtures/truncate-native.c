/* Original Darling ABI; explicit TLS/vchroot adapters and real sparse files. */
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
struct expanded {char path[4096];unsigned flags;int dfd;};
extern long sys_truncate(const char *,long long);
static int phase,cwd_fd,expansion_error,recording,calls;
static long raw_result;static long long expected_length;
int dearust_get_perthread_wd(void){assert(phase==0);phase=1;return cwd_fd;}
int vchroot_expand(struct expanded *args){
    assert(phase==1 && args->dfd==cwd_fd && args->flags==1 && memchr(args->path,0,4096));phase=2;
    if(expansion_error)return expansion_error;
    if(!recording){char absolute[4096];int n=snprintf(absolute,sizeof(absolute),"/proc/self/fd/%d/%s",cwd_fd,args->path);assert(n>0 && n<4096);memcpy(args->path,absolute,(size_t)n+1);}
    return 0;
}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){
    assert(phase==2 && nr==76 && a && b==expected_length && c==0 && d==0 && e==0 && f==0);phase=3;calls++;
    if(recording)return raw_result;
    long r=syscall(nr,a,b,c,d,e,f);return r<0?-errno:r;
}
static long run(const char *path,long long length){phase=0;expected_length=length;return sys_truncate(path,length);}
int main(void){
    char directory[]="/tmp/dearust-truncate-native-XXXXXX";assert(mkdtemp(directory));
    cwd_fd=open(directory,O_RDONLY|O_DIRECTORY);assert(cwd_fd>=0);
    int fd=openat(cwd_fd,"target",O_CREAT|O_RDWR,0600);assert(fd>=0 && write(fd,"kept",4)==4 && lseek(fd,2,SEEK_SET)==2);
    assert(symlinkat("target",cwd_fd,"link")==0);long long large=(INT64_C(1)<<32)+123;
    assert(run("link",large)==0);struct stat st;assert(fstat(fd,&st)==0 && st.st_size==large && lseek(fd,0,SEEK_CUR)==2);
    unsigned char bytes[6];memset(bytes,0xa5,sizeof(bytes));assert(pread(fd,bytes+1,4,0)==4 && memcmp(bytes+1,"kept",4)==0 && bytes[0]==0xa5 && bytes[5]==0xa5);
    assert(pread(fd,bytes+1,1,large-1)==1 && bytes[1]==0 && bytes[0]==0xa5);
    assert(run("target",-1)==-22 && run("target",INT64_MIN)==-22 && fstat(fd,&st)==0 && st.st_size==large);
    assert(run("missing",0)==-2);
    expansion_error=-11;int before=calls;assert(run("target",0)==-35 && phase==2 && calls==before);expansion_error=0;
    assert(run(NULL,0)==-14 && phase==0);char path[4096];memset(path,'x',4096);assert(run(path,0)==-63 && phase==1 && calls==before);
    recording=1;path[4095]=0;raw_result=0;assert(run(path,INT64_MAX)==0);
    raw_result=INT64_C(0x100000007);assert(run("recorded",INT64_MIN)==7);
    raw_result=INT64_C(0x1fffffff5);assert(run("recorded",-1)==-35);
    raw_result=-INT64_C(0x10000000b);assert(run("recorded",0)==-35);
    raw_result=-4;before=calls;assert(run("recorded",0)==-4 && calls==before+1);recording=0;
    assert(run("target",2)==0 && fstat(fd,&st)==0 && st.st_size==2 && lseek(fd,0,SEEK_CUR)==2);
    assert(close(fd)==0 && unlinkat(cwd_fd,"link",0)==0 && unlinkat(cwd_fd,"target",0)==0 && close(cwd_fd)==0 && rmdir(directory)==0);
    puts("truncate: TLS/expansion ordering, full length, bounded paths, raw widths and real sparse files");
}
