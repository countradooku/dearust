/* Same permissions workload, measuring individual four-call roundtrips.
 * Linux-pass-through rusage maxrss is KiB; see metadata-resource-guest.c. */
#define main permissions_timing_main
#include "permissions-guest-bench.c"
#undef main
#include "guest-resources.h"
static int compare_latency(const void *a,const void *b){uint64_t x=*(const uint64_t*)a,y=*(const uint64_t*)b;return (x>y)-(x<y);}
int main(void){
    alarm(60);char path[]="/private/tmp/dearust-resource-permissions-XXXXXX";
    assert(mkdtemp(path));int fd=open(path,O_RDONLY|O_DIRECTORY);assert(fd>=0);
    struct rusage before;assert(getrusage(RUSAGE_SELF,&before)==0);
    uint64_t cold=micros();roundtrip(fd);cold=micros()-cold;
    for(unsigned i=0;i<100;i++)roundtrip(fd);
    uint64_t times[512],c=cpu();
    for(unsigned i=0;i<512;i++){uint64_t start=micros();roundtrip(fd);times[i]=micros()-start;}
    c=cpu()-c;qsort(times,512,sizeof(times[0]),compare_latency);
    struct rusage usage;assert(getrusage(RUSAGE_SELF,&usage)==0);
    struct linux_memory_sample memory=resource_memory();
    fprintf(stderr,"PERMISSIONS_RESOURCE rounds=512 cpu_us=%llu first_round_us=%llu p50_us=%llu p95_us=%llu p99_us=%llu max_us=%llu maxrss_linux_kib=%ld initial_maxrss_linux_kib=%ld rss_linux_kib=%lu pss_linux_kib=%lu private_dirty_linux_kib=%lu\n",(unsigned long long)c,(unsigned long long)cold,(unsigned long long)times[255],(unsigned long long)times[486],(unsigned long long)times[506],(unsigned long long)times[511],usage.ru_maxrss,before.ru_maxrss,memory.rss,memory.pss,memory.private_dirty);
    assert(close(fd)==0 && rmdir(path)==0);puts("Permissions latency and resource checks passed");return 0;
}
