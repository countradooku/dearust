/* Explicit Darwin converter adapters; real Linux stat and injected ABI checks. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "guest-memory-native.h"
#include "darwin-stat-native.h"
extern long sys_fstat(int,void *),sys_fstat64(int,void *);
struct output {unsigned char before[16];_Alignas(8) unsigned char value[144];unsigned char after[16];};
static int injected,expected_fd,syscalls,conversions,phase;
static long raw_result;
_Static_assert(sizeof(struct stat)==144,"native Linux stat144");
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){
    long memory_result; if(dearust_native_memory_syscall(a,b,c,d,e,f,nr,&memory_result)) return memory_result;

    assert(phase==0 && nr==5 && a==expected_fd && b && !(b&7) && c==0 && d==0 && e==0 && f==0);phase=1;syscalls++;
    unsigned char zeros[144]={0};assert(memcmp((void *)b,zeros,144)==0);
    if(injected){memset((void *)b,0x6b,144);return raw_result;}
    long result=syscall(nr,a,b,c,d,e,f);return result<0?-errno:result;
}
void __getuidgid(int32_t *uid,int32_t *gid){
    assert(phase==1);phase=2;conversions++;*uid=(int32_t)getuid();*gid=(int32_t)getgid();
}
static long run(long (*call)(int,void *),int fd,void *output){phase=0;expected_fd=fd;return call(fd,output);}
static void guards(const struct output *out){for(unsigned i=0;i<16;i++)assert(out->before[i]==0xa5 && out->after[i]==0xa5);}
int main(void){
    char path[]="/tmp/dearust-fstat-native-XXXXXX";int fd=mkstemp(path);assert(fd>=0 && write(fd,"kept",4)==4);
    int duplicate=dup(fd);assert(duplicate>=0);struct stat reference;assert(fstat(fd,&reference)==0);
    for(int wide=0;wide<2;wide++){
        long (*call)(int,void *)=wide?sys_fstat64:sys_fstat;
        struct output out,before;memset(&out,0xa5,sizeof(out));
        assert(run(call,fd,&out.value)==0 && phase==2);guards(&out);
        assert(native_view(out.value,wide).st_ino==(wide?reference.st_ino:(uint32_t)reference.st_ino) && native_view(out.value,wide).st_size==4 && S_ISREG(native_view(out.value,wide).st_mode));
        assert(run(call,duplicate,&out.value)==0 && native_view(out.value,wide).st_ino==(wide?reference.st_ino:(uint32_t)reference.st_ino));
        before=out;int count=conversions;assert(run(call,-42,&out.value)==-9 && phase==1 && conversions==count && memcmp(&out,&before,sizeof(out))==0);
        assert(run(call,-1,NULL)==-9 && phase==1 && conversions==count);
        assert(run(call,fd,NULL)==-14 && phase==1 && conversions==count);
        injected=1;
        const long errors[]={-11,INT64_C(0x1fffffff5),-INT64_C(0x10000000b),-4};
        for(unsigned i=0;i<4;i++){raw_result=errors[i];int calls=syscalls;assert(run(call,INT32_MIN,&out.value)==(i==3?-4:-35) && syscalls==calls+1 && conversions==count && memcmp(&out,&before,sizeof(out))==0);}
        const long success[]={0,7,INT64_C(0x100000007),INT64_MIN};
        for(unsigned i=0;i<4;i++){raw_result=success[i];assert(run(call,INT32_MAX,&out.value)==0 && phase==2);guards(&out);assert(native_view(out.value,wide).st_size==INT64_C(0x6b6b6b6b6b6b6b6b) && native_view(out.value,wide).st_uid==getuid());assert(run(call,fd,NULL)==-14 && phase==1);}
        injected=0;
    }
    assert(syscalls==34 && conversions==12);
    assert(close(duplicate)==0 && close(fd)==0 && unlink(path)==0);
    puts("fstat/fstat64: two converters, initialized Linux stat, signed fd, raw widths, errors and NULL ordering");
}
