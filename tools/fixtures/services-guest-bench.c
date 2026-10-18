/* Two entropy requests and one raw calling-thread ID per allocation-free round. */
#include <assert.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <unistd.h>
static uint64_t expected_thread;
static uint64_t micros(void){struct timeval t;assert(gettimeofday(&t,NULL)==0);return (uint64_t)t.tv_sec*1000000+t.tv_usec;}
static uint64_t cpu(void){struct rusage r;assert(getrusage(RUSAGE_SELF,&r)==0);return ((uint64_t)r.ru_utime.tv_sec+r.ru_stime.tv_sec)*1000000+r.ru_utime.tv_usec+r.ru_stime.tv_usec;}
static void prepare(void){assert(pthread_threadid_np(NULL,&expected_thread)==0 && expected_thread>0);}
static void roundtrip(void){
    unsigned char small[34],large[258];small[0]=small[33]=0xa5;large[0]=large[257]=0xa5;
    assert(syscall(SYS_getentropy,small+1,32)==0 && syscall(SYS_getentropy,large+1,256)==0);
    assert(small[0]==0xa5 && small[33]==0xa5 && large[0]==0xa5 && large[257]==0xa5);
    long tid=syscall(SYS_thread_selfid);assert(tid>0 && (uint64_t)tid==expected_thread);
}
int main(void){
    alarm(60);prepare();for(unsigned i=0;i<100;i++)roundtrip();
    for(unsigned sample=0;sample<21;sample++){
        uint64_t c=cpu(),wall=micros();for(unsigned i=0;i<10000;i++)roundtrip();wall=micros()-wall;c=cpu()-c;
        fprintf(stderr,"SERVICES_BENCH sample=%u roundtrips=10000 cpu_us=%llu wall_us=%llu\n",sample,(unsigned long long)c,(unsigned long long)wall);
    }
    puts("Dearust entropy and thread services benchmark checks passed");return 0;
}
