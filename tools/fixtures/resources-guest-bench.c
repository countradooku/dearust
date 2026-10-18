/* Three resource calls per allocation-free round; same host effects for C/Rust. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <unistd.h>
static struct rlimit expected;
static uint64_t micros(void){struct timeval t;assert(gettimeofday(&t,NULL)==0);return (uint64_t)t.tv_sec*1000000+t.tv_usec;}
static uint64_t cpu(void){struct rusage r;assert(getrusage(RUSAGE_SELF,&r)==0);return ((uint64_t)r.ru_utime.tv_sec+r.ru_stime.tv_sec)*1000000+r.ru_utime.tv_usec+r.ru_stime.tv_usec;}
static void prepare(void){assert(getrlimit(RLIMIT_CORE,&expected)==0);}
static void roundtrip(void){
    struct rlimit r;struct rusage u;
    assert(syscall(SYS_getrlimit,RLIMIT_CORE,&r)==0 && r.rlim_cur==expected.rlim_cur && r.rlim_max==expected.rlim_max);
    assert(syscall(SYS_setrlimit,RLIMIT_CORE,&r)==0);
    assert(syscall(SYS_getrusage,RUSAGE_SELF,&u)==0);
    assert(u.ru_utime.tv_usec>=0 && u.ru_utime.tv_usec<1000000 && u.ru_stime.tv_usec>=0 && u.ru_stime.tv_usec<1000000);
}
int main(void){
    alarm(60);prepare();for(unsigned i=0;i<100;i++)roundtrip();
    for(unsigned sample=0;sample<21;sample++){
        uint64_t c=cpu(),wall=micros();for(unsigned i=0;i<10000;i++)roundtrip();wall=micros()-wall;c=cpu()-c;
        fprintf(stderr,"RESOURCES_BENCH sample=%u roundtrips=10000 cpu_us=%llu wall_us=%llu\n",sample,(unsigned long long)c,(unsigned long long)wall);
    }
    puts("Dearust resource benchmark checks passed");return 0;
}
