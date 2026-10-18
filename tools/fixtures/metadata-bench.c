/* Entry-wrapper comparison with shared canonical Rust fstatat backend.
 * Opaque output adapters copy Linux144 bytes: not native Darwin layout proof. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
extern long sys_fstat(int,void *),sys_fstat64(int,void *),sys_stat(const char *,void *),sys_stat64(const char *,void *);
struct expanded {char path[4096];unsigned flags;int dfd;};
struct output {unsigned char head[16];_Alignas(8) unsigned char opaque[256];unsigned char tail[16];};
_Static_assert(sizeof(struct stat)==144,"Linux x86_64 stat layout");
int atfd(int fd){return fd==-2?AT_FDCWD:fd;}
int atflags_bsd_to_linux(int flags){assert(flags==0);return 0;}
int get_perthread_wd(void){return AT_FDCWD;}
int dearust_get_perthread_wd(void){return AT_FDCWD;}
int vchroot_expand(struct expanded *args){(void)args;return 0;}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){long r=syscall(nr,a,b,c,d,e,f);return r<0?-errno:r;}
void stat_linux_to_bsd(const void *input,void *output){memcpy(output,input,144);}
void stat_linux_to_bsd64(const void *input,void *output){memcpy(output,input,144);}
static uint64_t nanos(clockid_t clock){struct timespec t;assert(clock_gettime(clock,&t)==0);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void roundtrip(int fd,const char *path,struct output out[4]){
    assert(sys_fstat(fd,out[0].opaque)==0);assert(sys_fstat64(fd,out[1].opaque)==0);
    assert(sys_stat(path,out[2].opaque)==0);assert(sys_stat64(path,out[3].opaque)==0);
}
int main(int argc,char **argv){
    assert(argc==2);unsigned n=(unsigned)strtoul(argv[1],NULL,10);assert(n>0);
    char path[]="/tmp/dearust-metadata-bench-XXXXXX";int fd=mkstemp(path);assert(fd>=0 && write(fd,"payload",7)==7);
    struct output out[4];memset(out,0xa5,sizeof(out));
    for(unsigned i=0;i<100;i++)roundtrip(fd,path,out);
    uint64_t c=nanos(CLOCK_THREAD_CPUTIME_ID),w=nanos(CLOCK_MONOTONIC);
    for(unsigned i=0;i<n;i++)roundtrip(fd,path,out);
    w=nanos(CLOCK_MONOTONIC)-w;c=nanos(CLOCK_THREAD_CPUTIME_ID)-c;
    struct stat reference;assert(fstat(fd,&reference)==0);
    for(unsigned i=0;i<4;i++){
        struct stat value;memcpy(&value,out[i].opaque,sizeof(value));
        assert(value.st_ino==reference.st_ino && value.st_size==7 && S_ISREG(value.st_mode));
        assert(memcmp(out[0].opaque,out[i].opaque,144)==0);
        for(unsigned j=0;j<16;j++)assert(out[i].head[j]==0xa5 && out[i].tail[j]==0xa5);
        for(unsigned j=144;j<256;j++)assert(out[i].opaque[j]==0xa5);
    }
    assert(close(fd)==0 && unlink(path)==0);
    printf("%u %llu %llu\n",n,(unsigned long long)c,(unsigned long long)w);return 0;
}
