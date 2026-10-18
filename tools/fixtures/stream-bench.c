#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
extern long sys_read(int,void*,size_t),sys_read_nocancel(int,void*,size_t);
extern long sys_write(int,const void*,size_t),sys_write_nocancel(int,const void*,size_t);
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){long r=syscall(nr,a,b,c,d,e,f);return r<0?-errno:r;}
long sys_pthread_canceled(int action){assert(action==0);return -22;}
long sys_getrlimit(unsigned which,void *limit){(void)which;(void)limit;abort();}
long __real_ioctl(int fd,int request,void *argument){long r=ioctl(fd,(unsigned int)request,argument);return r<0?-errno:r;}
static uint64_t nanos(clockid_t id){struct timespec t;assert(clock_gettime(id,&t)==0);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static void roundtrip(int input,int output,unsigned char data[128]){
    assert(sys_read_nocancel(input,data,128)==128 && sys_write_nocancel(output,data,128)==128);
    assert(sys_read(input,data,128)==128 && sys_write(output,data,128)==128);
}
int main(int argc,char **argv){
    assert(argc==2);unsigned n=(unsigned)strtoul(argv[1],NULL,10);assert(n>0);
    int input=open("/dev/zero",O_RDONLY),output=open("/dev/null",O_WRONLY);assert(input>=0 && output>=0);
    unsigned char data[128];for(unsigned i=0;i<200;i++)roundtrip(input,output,data);
    uint64_t c=nanos(CLOCK_THREAD_CPUTIME_ID),w=nanos(CLOCK_MONOTONIC);
    for(unsigned i=0;i<n;i++)roundtrip(input,output,data);
    w=nanos(CLOCK_MONOTONIC)-w;c=nanos(CLOCK_THREAD_CPUTIME_ID)-c;
    for(unsigned i=0;i<sizeof(data);i++)assert(data[i]==0);
    assert(close(input)==0 && close(output)==0);
    printf("%u %llu %llu\n",n,(unsigned long long)c,(unsigned long long)w);return 0;
}
