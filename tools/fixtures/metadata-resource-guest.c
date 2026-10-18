/* Same four-call workload; one-microsecond wall clock resolution. Darling's
 * unchanged sys_getrusage passes Linux rusage through: maxrss is Linux KiB,
 * not the bytes specified by the macOS API. No general application RSS claim. */
#define METADATA_VERIFY_ONLY
#define main metadata_timing_main
#include "metadata-guest-bench.c"
#undef main
#include "guest-resources.h"
static int compare_latency(const void *a,const void *b){uint64_t x=*(const uint64_t*)a,y=*(const uint64_t*)b;return (x>y)-(x<y);}
int main(void){
    alarm(60);char path[]="/private/tmp/dearust-resource-metadata-XXXXXX";
    int fd=mkstemp(path);assert(fd>=0 && write(fd,"payload",7)==7);
    struct narrow n[2];struct wide w[2];memset(n,0xa5,sizeof(n));memset(w,0xa5,sizeof(w));
    struct rusage before;assert(getrusage(RUSAGE_SELF,&before)==0);
    uint64_t cold=micros();roundtrip(fd,path,n,w);cold=micros()-cold;
    for(unsigned i=0;i<100;i++)roundtrip(fd,path,n,w);
    uint64_t times[512],c=cpu();
    for(unsigned i=0;i<512;i++){uint64_t start=micros();roundtrip(fd,path,n,w);times[i]=micros()-start;}
    c=cpu()-c;check_metadata(fd,n,w);qsort(times,512,sizeof(times[0]),compare_latency);
    struct rusage usage;assert(getrusage(RUSAGE_SELF,&usage)==0);
    struct linux_memory_sample memory=resource_memory();
    fprintf(stderr,"METADATA_RESOURCE rounds=512 cpu_us=%llu first_round_us=%llu p50_us=%llu p95_us=%llu p99_us=%llu max_us=%llu maxrss_linux_kib=%ld initial_maxrss_linux_kib=%ld rss_linux_kib=%lu pss_linux_kib=%lu private_dirty_linux_kib=%lu\n",(unsigned long long)c,(unsigned long long)cold,(unsigned long long)times[255],(unsigned long long)times[486],(unsigned long long)times[506],(unsigned long long)times[511],usage.ru_maxrss,before.ru_maxrss,memory.rss,memory.pss,memory.private_dirty);
    assert(close(fd)==0 && unlink(path)==0);puts("Metadata latency and resource checks passed");return 0;
}
