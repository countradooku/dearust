/* Same calls; process quantiles with1us resolution and raw Linux memory units. */
#define main resources_timing_main
#include "resources-guest-bench.c"
#undef main
#include "guest-resources.h"
static int compare_latency(const void *a,const void *b){uint64_t x=*(const uint64_t*)a,y=*(const uint64_t*)b;return (x>y)-(x<y);}
static long linux_peak(void){
    int64_t usage[18];long result;
    __asm__ volatile("syscall":"=a"(result):"a"(98L),"D"(0L),"S"(usage):"rcx","r11","memory");
    assert(result==0);return usage[4];
}
int main(void){
    alarm(60);prepare();long before=linux_peak();
    uint64_t first=micros();roundtrip();first=micros()-first;for(unsigned i=0;i<100;i++)roundtrip();
    uint64_t times[512],c=cpu();for(unsigned i=0;i<512;i++){uint64_t start=micros();roundtrip();times[i]=micros()-start;}c=cpu()-c;
    qsort(times,512,sizeof(times[0]),compare_latency);long peak=linux_peak();
    struct linux_memory_sample memory=resource_memory();
    fprintf(stderr,"RESOURCES_RESOURCE rounds=512 cpu_us=%llu first_round_us=%llu p50_us=%llu p95_us=%llu p99_us=%llu max_us=%llu maxrss_linux_kib=%ld initial_maxrss_linux_kib=%ld rss_linux_kib=%lu pss_linux_kib=%lu private_dirty_linux_kib=%lu\n",(unsigned long long)c,(unsigned long long)first,(unsigned long long)times[255],(unsigned long long)times[486],(unsigned long long)times[506],(unsigned long long)times[511],peak,before,memory.rss,memory.pss,memory.private_dirty);
    puts("Dearust resource latency and memory checks passed");return 0;
}
