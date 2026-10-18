/* Actual x86_64 Darwin resource ABI, safe copy faults, and host-backed policy. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
_Static_assert(sizeof(struct rlimit)==16,"Darwin rlimit16");
_Static_assert(sizeof(struct rusage)==144,"Darwin rusage144");
_Static_assert(sizeof(((struct timeval*)0)->tv_usec)==4,"Darwin usec32");
_Static_assert(offsetof(struct rusage,ru_maxrss)==32,"Darwin maxrss offset32");
_Static_assert(RLIM_INFINITY==INT64_MAX,"Darwin infinity63bits");
static long linux_call(long nr,long a,long b,long c,long d){
    register long r10 __asm__("r10")=d;long ret;
    __asm__ volatile("syscall":"=a"(ret):"a"(nr),"D"(a),"S"(b),"d"(c),"r"(r10):"rcx","r11","memory");return ret;
}
static void limits(void){
    const unsigned map[]={0,1,2,3,4,9,8,6,7};
    for(unsigned which=0;which<9;which++){
        uint64_t host[2];assert(linux_call(302,0,map[which],0,(long)host)==0);
        unsigned char bytes[18];memset(bytes,0xa5,sizeof(bytes));
        assert(syscall(SYS_getrlimit,which,bytes+1)==0);
        uint64_t guest[2];memcpy(guest,bytes+1,16);
        assert(bytes[0]==0xa5 && bytes[17]==0xa5);
        for(unsigned i=0;i<2;i++)assert(guest[i]==(host[i]==UINT64_MAX?(uint64_t)RLIM_INFINITY:host[i]));
        struct rlimit flagged;assert(syscall(SYS_getrlimit,which|0x1000,&flagged)==0);
        assert(memcmp(&flagged,guest,16)==0);
    }
    struct rlimit limit;assert(getrlimit(RLIMIT_NOFILE,&limit)==0);
    assert(getdtablesize()==(int)(limit.rlim_cur>INT32_MAX?INT32_MAX:limit.rlim_cur));
    unsigned char untouched[16];memset(untouched,0xa5,16);
    for(unsigned which=9;which<12;which++){
        errno=0;assert(syscall(SYS_getrlimit,which,NULL)==-1 && errno==EINVAL);
        errno=0;assert(syscall(SYS_getrlimit,which,untouched)==-1 && errno==EINVAL);
        errno=0;assert(syscall(SYS_setrlimit,which,NULL)==-1 && errno==EFAULT);
        errno=0;assert(syscall(SYS_setrlimit,which,&limit)==-1 && errno==EINVAL);
    }
    for(unsigned i=0;i<16;i++)assert(untouched[i]==0xa5);
    struct rlimit invalid={2,1};errno=0;assert(syscall(SYS_setrlimit,RLIMIT_NOFILE,&invalid)==-1 && errno==EINVAL);
    struct rlimit unrepresentable={0,UINT64_MAX};errno=0;assert(syscall(SYS_setrlimit,RLIMIT_NOFILE,&unrepresentable)==-1 && errno==EOVERFLOW);
    struct rlimit after;assert(getrlimit(RLIMIT_NOFILE,&after)==0 && memcmp(&after,&limit,16)==0);
}
static void usage(void){
    unsigned char output[146];memset(output,0xa5,sizeof(output));
    assert(syscall(SYS_getrusage,RUSAGE_SELF,output+1)==0);
    assert(output[0]==0xa5 && output[145]==0xa5);
    for(unsigned i=13;i<17;i++)assert(output[i]==0);
    for(unsigned i=29;i<33;i++)assert(output[i]==0);
    struct rusage darwin;memcpy(&darwin,output+1,sizeof(darwin));
    int64_t linux_usage[18];assert(linux_call(98,0,(long)linux_usage,0,0)==0);
    assert(darwin.ru_maxrss==linux_usage[4]*1024 && darwin.ru_maxrss>1024*1024);
    assert(darwin.ru_utime.tv_usec>=0 && darwin.ru_utime.tv_usec<1000000);
    assert(darwin.ru_stime.tv_usec>=0 && darwin.ru_stime.tv_usec<1000000);
    struct rusage children;assert(getrusage(RUSAGE_CHILDREN,&children)==0);
    const int invalid[]={1,2,-2,INT32_MIN,INT32_MAX};
    for(unsigned i=0;i<sizeof(invalid)/sizeof(invalid[0]);i++){
        errno=0;assert(syscall(SYS_getrusage,invalid[i],NULL)==-1 && errno==EINVAL);
        errno=0;assert(syscall(SYS_getrusage,invalid[i],output+1)==-1 && errno==EINVAL);
    }
}
static void faults(void){
    size_t page=(size_t)sysconf(_SC_PAGESIZE);assert(page>=4096);
    unsigned char *memory=mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);assert(memory!=MAP_FAILED);
    memset(memory,0xa5,page*2);assert(mprotect(memory+page,page,PROT_NONE)==0);
    void *bad[]={NULL,(void*)1,memory+page,memory+page-8};
    struct rlimit before,after;assert(getrlimit(RLIMIT_CORE,&before)==0);
    for(unsigned i=0;i<4;i++){
        errno=0;assert(syscall(SYS_getrlimit,RLIMIT_CORE,bad[i])==-1 && errno==EFAULT);
        errno=0;assert(syscall(SYS_setrlimit,RLIMIT_CORE,bad[i])==-1 && errno==EFAULT);
        errno=0;assert(syscall(SYS_getrusage,RUSAGE_SELF,bad[i])==-1 && errno==EFAULT);
        assert(getrlimit(RLIMIT_CORE,&after)==0 && memcmp(&before,&after,16)==0);
    }
    assert(mprotect(memory,page,PROT_READ)==0);
    errno=0;assert(syscall(SYS_getrlimit,RLIMIT_CORE,memory)==-1 && errno==EFAULT);
    errno=0;assert(syscall(SYS_getrusage,RUSAGE_SELF,memory)==-1 && errno==EFAULT);
    assert(mprotect(memory,page,PROT_READ|PROT_WRITE)==0);
    memcpy(memory+1,&before,16);assert(mprotect(memory,page,PROT_READ)==0);
    assert(syscall(SYS_setrlimit,RLIMIT_CORE,memory+1)==0);
    assert(munmap(memory,page*2)==0);
}
static void pressure(unsigned value){
    pid_t child=fork();assert(child>=0);
    if(child==0){
        struct rlimit limit={(rlim_t)value,(rlim_t)value};
        assert(syscall(SYS_setrlimit,RLIMIT_NOFILE,&limit)==0);
        struct rlimit current;assert(syscall(SYS_getrlimit,RLIMIT_NOFILE,&current)==0);
        assert(current.rlim_cur==value && current.rlim_max==value && getdtablesize()==(int)value);
        uint64_t host[2];assert(linux_call(302,0,7,0,(long)host)==0 && host[0]==value && host[1]==value);
        struct rusage ru;assert(syscall(SYS_getrusage,RUSAGE_SELF,&ru)==0);
        if(value==64){
            int fds[64],count=0,fd;
            while((fd=open("/dev/null",O_RDONLY))>=0){assert(count<64);fds[count++]=fd;assert(fd<64);}
            assert(errno==EMFILE);
            assert(getrlimit(RLIMIT_NOFILE,&current)==0 && current.rlim_cur==64);
            assert(getrusage(RUSAGE_SELF,&ru)==0);
            while(count)assert(close(fds[--count])==0);
        }
        _exit(0);
    }
    int status;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
}
static void child_accounting(void){
    struct rusage before,after;assert(getrusage(RUSAGE_CHILDREN,&before)==0);
    pid_t child=fork();assert(child>=0);
    if(child==0){
        size_t length=16*1024*1024;volatile unsigned char *bytes=mmap(NULL,length,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);assert(bytes!=MAP_FAILED);
        for(size_t i=0;i<length;i+=4096)bytes[i]=(unsigned char)i;
        volatile uint64_t total=0;for(unsigned i=0;i<10000000;i++)total+=i;
        assert(total>0);_exit(0);
    }
    int status;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
    assert(getrusage(RUSAGE_CHILDREN,&after)==0);
    int64_t host[18];assert(linux_call(98,-1,(long)host,0,0)==0);
    assert(after.ru_maxrss==host[4]*1024 && after.ru_maxrss>=16*1024*1024);
    uint64_t b=((uint64_t)before.ru_utime.tv_sec+before.ru_stime.tv_sec)*1000000+before.ru_utime.tv_usec+before.ru_stime.tv_usec;
    uint64_t a=((uint64_t)after.ru_utime.tv_sec+after.ru_stime.tv_sec)*1000000+after.ru_utime.tv_usec+after.ru_stime.tv_usec;
    assert(a>b);
}
int main(void){
    alarm(60);limits();usage();faults();pressure(0);pressure(1);pressure(64);child_accounting();
    puts("Dearust resource ABI, faults and pressure checks passed");return 0;
}
