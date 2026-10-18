/* Same four-call workload and Linux KiB memory counters; clock resolution1us. */
#define main stream_timing_main
#include "stream-guest-bench.c"
#undef main
#include "guest-resources.h"
#include <stdlib.h>
static int compare_latency(const void *a,const void *b){uint64_t x=*(const uint64_t*)a,y=*(const uint64_t*)b;return (x>y)-(x<y);}
int main(void){
    alarm(60);int input=open("/dev/zero",O_RDONLY),output=open("/dev/null",O_WRONLY);assert(input>=0 && output>=0);
    unsigned char data[128];memset(data,0xa5,sizeof(data));struct rusage before;assert(getrusage(RUSAGE_SELF,&before)==0);
    uint64_t first=micros();roundtrip(input,output,data);first=micros()-first;
    for(unsigned i=0;i<100;i++)roundtrip(input,output,data);
    uint64_t times[512],c=cpu();
    for(unsigned i=0;i<512;i++){uint64_t start=micros();roundtrip(input,output,data);times[i]=micros()-start;}
    c=cpu()-c;for(unsigned i=0;i<sizeof(data);i++)assert(data[i]==0);
    qsort(times,512,sizeof(times[0]),compare_latency);struct rusage usage;assert(getrusage(RUSAGE_SELF,&usage)==0);
    struct linux_memory_sample memory=resource_memory();
    fprintf(stderr,"STREAM_RESOURCE rounds=512 cpu_us=%llu first_round_us=%llu p50_us=%llu p95_us=%llu p99_us=%llu max_us=%llu maxrss_linux_kib=%ld initial_maxrss_linux_kib=%ld rss_linux_kib=%lu pss_linux_kib=%lu private_dirty_linux_kib=%lu\n",(unsigned long long)c,(unsigned long long)first,(unsigned long long)times[255],(unsigned long long)times[486],(unsigned long long)times[506],(unsigned long long)times[511],usage.ru_maxrss,before.ru_maxrss,memory.rss,memory.pss,memory.private_dirty);
    assert(close(input)==0 && close(output)==0);puts("Stream latency and resource checks passed");return 0;
}
