/* Eight traditional pathname operations, existing dirfd TLS, owned fixture. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <unistd.h>
extern int pthread_fchdir_np(int);
static uint64_t micros(void){struct timeval t;assert(gettimeofday(&t,NULL)==0);return (uint64_t)t.tv_sec*1000000+t.tv_usec;}
static uint64_t cpu(void){struct rusage r;assert(getrusage(RUSAGE_SELF,&r)==0);return ((uint64_t)r.ru_utime.tv_sec+r.ru_stime.tv_sec)*1000000+r.ru_utime.tv_usec+r.ru_stime.tv_usec;}
static void roundtrip(void){
    char bytes[16];memset(bytes,0xa5,sizeof(bytes));
    assert(syscall(SYS_mkdir,"sub",0700)==0 && syscall(SYS_symlink,"seed","soft")==0);
    assert(syscall(SYS_readlink,"soft",bytes,sizeof(bytes))==4 && memcmp(bytes,"seed",4)==0 && (unsigned char)bytes[4]==0xa5);
    assert(syscall(SYS_link,"seed","hard")==0 && syscall(SYS_rename,"hard","moved")==0);
    assert(syscall(SYS_unlink,"moved")==0 && syscall(SYS_unlink,"soft")==0 && rmdir("sub")==0);
}
int main(void){
    alarm(60);char root[]="/private/tmp/dearust-frontends-bench-XXXXXX";assert(mkdtemp(root));
    int fd=open(root,O_RDONLY);assert(fd>=0 && pthread_fchdir_np(fd)==0);
    int seed=openat(fd,"seed",O_CREAT|O_EXCL|O_RDWR,0600);assert(seed>=0 && write(seed,"kept",4)==4);
    for(unsigned i=0;i<100;i++)roundtrip();
    for(unsigned sample=0;sample<21;sample++){
        uint64_t c=cpu(),wall=micros();for(unsigned i=0;i<200;i++)roundtrip();wall=micros()-wall;c=cpu()-c;
        fprintf(stderr,"FRONTENDS_BENCH sample=%u roundtrips=200 cpu_us=%llu wall_us=%llu\n",sample,(unsigned long long)c,(unsigned long long)wall);
    }
    struct stat st;char data[4];assert(fstat(seed,&st)==0 && st.st_size==4 && st.st_nlink==1 && pread(seed,data,4,0)==4 && memcmp(data,"kept",4)==0);
    assert(close(seed)==0 && unlinkat(fd,"seed",0)==0 && pthread_fchdir_np(-1)==0 && close(fd)==0 && rmdir(root)==0);
    puts("Dearust traditional pathname benchmark checks passed");return 0;
}
