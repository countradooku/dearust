/* Actual guest region-query correctness and CPU/wall samples; no golden timing. */
#include <assert.h>
#include <fcntl.h>
#include <libproc.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>
static uint64_t micros(void) {struct timeval t;assert(gettimeofday(&t,NULL)==0);return (uint64_t)t.tv_sec*1000000+t.tv_usec;}
static uint64_t cpu(void) {struct rusage r;assert(getrusage(RUSAGE_SELF,&r)==0);return ((uint64_t)r.ru_utime.tv_sec+r.ru_stime.tv_sec)*1000000+r.ru_utime.tv_usec+r.ru_stime.tv_usec;}
int main(void) {
    alarm(60);
    char name[]="/private/tmp/dearust-region-bench-XXXXXX";int fd=mkstemp(name);assert(fd>=0&&ftruncate(fd,4096)==0);
    struct stat sb;assert(fstat(fd,&sb)==0);
    void *maps[128];uint64_t address=0;
    for(unsigned i=0;i<128;i++){maps[i]=mmap(NULL,4096,PROT_READ,MAP_PRIVATE,fd,0);assert(maps[i]!=MAP_FAILED);if((uint64_t)maps[i]>address)address=(uint64_t)maps[i];}
    assert(close(fd)==0);
    const unsigned flavors[]={PROC_PIDREGIONPATHINFO,22};
    for(unsigned f=0;f<2;f++)for(unsigned sample=0;sample<21;sample++) {
        struct proc_regionwithpathinfo info;uint64_t c=cpu(),w=micros();
        for(unsigned i=0;i<50;i++) {
            assert(proc_pidinfo(getpid(),flavors[f],address,&info,sizeof(info))==sizeof(info));
            assert(info.prp_prinfo.pri_address==address&&info.prp_vip.vip_vi.vi_stat.vst_ino==sb.st_ino);
        }
        w=micros()-w;c=cpu()-c;
        fprintf(stderr,"REGION_BENCH flavor=%u sample=%u calls=50 cpu_us=%llu wall_us=%llu\n",flavors[f],sample,(unsigned long long)c,(unsigned long long)w);
    }
    for(unsigned i=0;i<128;i++)assert(munmap(maps[i],4096)==0);
    assert(unlink(name)==0);puts("Dearust region query benchmark checks passed");return 0;
}
