#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
extern long sys_getentropy(void *, size_t), sys_thread_selfid(void);
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){long r=syscall(nr,a,b,c,d,e,f);return r<0?-errno:r;}
static uint64_t nanos(clockid_t id){struct timespec t;assert(clock_gettime(id,&t)==0);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void roundtrip(long id,unsigned char data[258]){
    assert(sys_getentropy(data+1,32)==0 && sys_getentropy(data+1,256)==0);
    assert(sys_thread_selfid()==id && data[0]==0xa5 && data[257]==0xa5);
}
int main(int argc,char **argv){
    assert(argc==2);unsigned n=(unsigned)strtoul(argv[1],NULL,10);assert(n>0);
    long id=syscall(SYS_gettid);assert(id>0);unsigned char data[258];memset(data,0xa5,sizeof(data));
    for(unsigned i=0;i<200;i++)roundtrip(id,data);
    uint64_t c=nanos(CLOCK_THREAD_CPUTIME_ID),w=nanos(CLOCK_MONOTONIC);
    for(unsigned i=0;i<n;i++)roundtrip(id,data);
    w=nanos(CLOCK_MONOTONIC)-w;c=nanos(CLOCK_THREAD_CPUTIME_ID)-c;
    printf("%u %llu %llu\n",n,(unsigned long long)c,(unsigned long long)w);return 0;
}
