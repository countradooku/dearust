/* Actual guest permissions CPU samples; timings are not correctness goldens. */
#include <assert.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>
static uint64_t micros(void){struct timeval t;assert(gettimeofday(&t,NULL)==0);return (uint64_t)t.tv_sec*1000000+t.tv_usec;}
static uint64_t cpu(void){struct rusage r;assert(getrusage(RUSAGE_SELF,&r)==0);return ((uint64_t)r.ru_utime.tv_sec+r.ru_stime.tv_sec)*1000000+r.ru_utime.tv_usec+r.ru_stime.tv_usec;}
static void roundtrip(int a){assert(mkdirat(a,"entry",0700)==0);assert(fchmodat(a,"entry",0700,0)==0);assert(faccessat(a,"entry",R_OK|X_OK,0)==0);assert(unlinkat(a,"entry",AT_REMOVEDIR)==0);}
int main(void){
    alarm(60);char temp[]="/private/tmp/dearust-permissions-bench-XXXXXX";assert(mkdtemp(temp));
    int a=open(temp,O_RDONLY|O_DIRECTORY);assert(a>=0);
    for(unsigned i=0;i<100;i++)roundtrip(a);
    for(unsigned sample=0;sample<21;sample++){
        uint64_t c=cpu(),w=micros();for(unsigned i=0;i<200;i++)roundtrip(a);w=micros()-w;c=cpu()-c;
        fprintf(stderr,"PERMISSIONS_BENCH sample=%u roundtrips=200 cpu_us=%llu wall_us=%llu\n",sample,(unsigned long long)c,(unsigned long long)w);
    }
    assert(close(a)==0&&rmdir(temp)==0);
    puts("Dearust guest permissions benchmark checks passed");return 0;
}
