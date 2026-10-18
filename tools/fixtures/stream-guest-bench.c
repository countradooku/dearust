/* Identical raw Darwin read/write workloads; no guest-pointer conversion adapter. */
#include <assert.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <unistd.h>
static uint64_t micros(void){struct timeval t;assert(gettimeofday(&t,NULL)==0);return (uint64_t)t.tv_sec*1000000+t.tv_usec;}
static uint64_t cpu(void){struct rusage r;assert(getrusage(RUSAGE_SELF,&r)==0);return ((uint64_t)r.ru_utime.tv_sec+r.ru_stime.tv_sec)*1000000+r.ru_utime.tv_usec+r.ru_stime.tv_usec;}
static void roundtrip(int input,int output,unsigned char data[128]){
    assert(syscall(SYS_read_nocancel,input,data,128)==128);
    assert(syscall(SYS_write_nocancel,output,data,128)==128);
    assert(syscall(SYS_read,input,data,128)==128);
    assert(syscall(SYS_write,output,data,128)==128);
}
int main(void){
    alarm(60);int input=open("/dev/zero",O_RDONLY),output=open("/dev/null",O_WRONLY);assert(input>=0 && output>=0);
    unsigned char data[128];memset(data,0xa5,sizeof(data));
    for(unsigned i=0;i<200;i++)roundtrip(input,output,data);
    for(unsigned sample=0;sample<21;sample++){
        uint64_t c=cpu(),wall=micros();for(unsigned i=0;i<5000;i++)roundtrip(input,output,data);wall=micros()-wall;c=cpu()-c;
        fprintf(stderr,"STREAM_BENCH sample=%u roundtrips=5000 cpu_us=%llu wall_us=%llu\n",sample,(unsigned long long)c,(unsigned long long)wall);
    }
    for(unsigned i=0;i<sizeof(data);i++)assert(data[i]==0);
    assert(close(input)==0 && close(output)==0);puts("Dearust stream benchmark checks passed");return 0;
}
