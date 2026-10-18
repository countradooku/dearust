/* Native C frontends + identical Rust at backend vs all-Rust entry chain. */
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
#include <time.h>
#include <unistd.h>
extern long sys_mkdir(const char*,unsigned),sys_symlink(const char*,const char*),sys_readlink(const char*,char*,int),sys_link(const char*,const char*),sys_rename(const char*,const char*),sys_unlink(const char*),sys_rmdir(const char*);
struct expanded{char path[4096];unsigned flags;int dfd;};
static int cwd_fd;
int dearust_get_perthread_wd(void){return cwd_fd;}
int dearust_copy_signals_ready(void){return 0;}
int atfd(int fd){return fd==-2?cwd_fd:fd;}
int atflags_bsd_to_linux(int flags){assert(flags==0);return 0;}
int vchroot_expand(struct expanded *p){assert(p->dfd==cwd_fd && p->flags==0);return 0;}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){long result=syscall(nr,a,b,c,d,e,f);return result<0?-errno:result;}
static uint64_t nanos(clockid_t clock){struct timespec t;assert(clock_gettime(clock,&t)==0);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void roundtrip(void){
    char data[16];memset(data,0xa5,sizeof(data));
    assert(sys_mkdir("sub",0700)==0 && sys_symlink("seed","soft")==0);
    assert(sys_readlink("soft",data,sizeof(data))==4 && memcmp(data,"seed",4)==0 && (unsigned char)data[4]==0xa5);
    assert(sys_link("seed","hard")==0 && sys_rename("hard","moved")==0);
    assert(sys_unlink("moved")==0 && sys_unlink("soft")==0 && sys_rmdir("sub")==0);
}
int main(int argc,char **argv){
    assert(argc==2);unsigned n=(unsigned)strtoul(argv[1],NULL,10);assert(n>0);
    char root[]="/tmp/dearust-frontends-bench-XXXXXX";assert(mkdtemp(root));cwd_fd=open(root,O_RDONLY|O_DIRECTORY);assert(cwd_fd>=0);
    int seed=openat(cwd_fd,"seed",O_CREAT|O_EXCL|O_RDWR,0600);assert(seed>=0 && write(seed,"kept",4)==4);
    for(unsigned i=0;i<100;i++)roundtrip();
    uint64_t c=nanos(CLOCK_THREAD_CPUTIME_ID),w=nanos(CLOCK_MONOTONIC);
    for(unsigned i=0;i<n;i++)roundtrip();w=nanos(CLOCK_MONOTONIC)-w;c=nanos(CLOCK_THREAD_CPUTIME_ID)-c;
    struct stat st;char data[4];assert(fstat(seed,&st)==0 && st.st_size==4 && st.st_nlink==1 && pread(seed,data,4,0)==4 && memcmp(data,"kept",4)==0);
    assert(close(seed)==0 && unlinkat(cwd_fd,"seed",0)==0 && close(cwd_fd)==0 && rmdir(root)==0);
    printf("%u %llu %llu\n",n,(unsigned long long)c,(unsigned long long)w);return 0;
}
