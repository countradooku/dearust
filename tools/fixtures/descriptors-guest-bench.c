/* Actual guest descriptor operation timing; CPU/wall samples are not goldens. */
#include <assert.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>
#include <sys/syscall.h>
/* Installed SDK declares SYS_fdatasync but libc has no public fdatasync symbol. */
static int data_sync(int fd){return (int)syscall(SYS_fdatasync,fd);}
static uint64_t micros(void){struct timeval t;assert(gettimeofday(&t,NULL)==0);return (uint64_t)t.tv_sec*1000000+t.tv_usec;}
static uint64_t cpu(void){struct rusage r;assert(getrusage(RUSAGE_SELF,&r)==0);return ((uint64_t)r.ru_utime.tv_sec+r.ru_stime.tv_sec)*1000000+r.ru_utime.tv_usec+r.ru_stime.tv_usec;}
static void roundtrip(int fd){assert(fchmod(fd,0600)==0);assert(ftruncate(fd,128)==0);assert(lseek(fd,64,SEEK_SET)==64);assert(flock(fd,LOCK_EX|LOCK_NB)==0);assert(flock(fd,LOCK_UN)==0);assert(fsync(fd)==0);assert(data_sync(fd)==0);}
int main(void){
    alarm(60);char path[]="/private/tmp/dearust-descriptors-bench-XXXXXX";int fd=mkstemp(path);assert(fd>=0);assert(unlink(path)==0);
    for(unsigned i=0;i<100;i++)roundtrip(fd);
    for(unsigned sample=0;sample<21;sample++){
        uint64_t c=cpu(),w=micros();for(unsigned i=0;i<200;i++)roundtrip(fd);w=micros()-w;c=cpu()-c;
        fprintf(stderr,"DESCRIPTORS_BENCH sample=%u roundtrips=200 cpu_us=%llu wall_us=%llu\n",sample,(unsigned long long)c,(unsigned long long)w);
    }
    struct stat st;assert(fstat(fd,&st)==0 && st.st_size==128 && (st.st_mode&0777)==0600);
    assert(lseek(fd,0,SEEK_CUR)==64);
    assert(close(fd)==0);puts("Dearust guest descriptor benchmark checks passed");return 0;
}
