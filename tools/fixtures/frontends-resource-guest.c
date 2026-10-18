/* Same eight pathname calls; per-process quantiles with1us clock resolution. */
#define main frontends_timing_main
#include "frontends-guest-bench.c"
#undef main
#include "guest-resources.h"
static int compare_latency(const void *a,const void *b){uint64_t x=*(const uint64_t*)a,y=*(const uint64_t*)b;return (x>y)-(x<y);}
int main(void){
    alarm(60);char root[]="/private/tmp/dearust-frontends-resource-XXXXXX";assert(mkdtemp(root));int fd=open(root,O_RDONLY);assert(fd>=0 && pthread_fchdir_np(fd)==0);
    int seed=openat(fd,"seed",O_CREAT|O_EXCL|O_RDWR,0600);assert(seed>=0 && write(seed,"kept",4)==4);
    struct rusage before;assert(getrusage(RUSAGE_SELF,&before)==0);
    uint64_t first=micros();roundtrip();first=micros()-first;for(unsigned i=0;i<100;i++)roundtrip();
    uint64_t times[512],c=cpu();for(unsigned i=0;i<512;i++){uint64_t start=micros();roundtrip();times[i]=micros()-start;}c=cpu()-c;
    qsort(times,512,sizeof(times[0]),compare_latency);struct rusage usage;assert(getrusage(RUSAGE_SELF,&usage)==0);
    struct linux_memory_sample memory=resource_memory();
    fprintf(stderr,"FRONTENDS_RESOURCE rounds=512 cpu_us=%llu first_round_us=%llu p50_us=%llu p95_us=%llu p99_us=%llu max_us=%llu maxrss_linux_kib=%ld initial_maxrss_linux_kib=%ld rss_linux_kib=%lu pss_linux_kib=%lu private_dirty_linux_kib=%lu\n",(unsigned long long)c,(unsigned long long)first,(unsigned long long)times[255],(unsigned long long)times[486],(unsigned long long)times[506],(unsigned long long)times[511],usage.ru_maxrss,before.ru_maxrss,memory.rss,memory.pss,memory.private_dirty);
    struct stat st;assert(fstat(seed,&st)==0 && st.st_size==4 && st.st_nlink==1);
    assert(close(seed)==0 && unlinkat(fd,"seed",0)==0 && pthread_fchdir_np(-1)==0 && close(fd)==0 && rmdir(root)==0);
    puts("Traditional pathname latency and resource checks passed");return 0;
}
