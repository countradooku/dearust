/* SPDX-License-Identifier: GPL-3.0-or-later
 * Real resource ABI fixture. Copy adapters use kernel-validated self-memory;
 * the independent guarded-copy fixture tests production signal recovery. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/uio.h>
#include <sys/wait.h>
#include <unistd.h>
extern long sys_getrlimit(unsigned, void *), sys_setrlimit(unsigned, const void *);
extern long sys_getrusage(int, void *), sys_getdtablesize(void);
extern unsigned rlimit_bsd_to_linux(unsigned);
extern int errno_linux_to_bsd(int);
/* Only grouped fd object's getdtablesize is exercised. Unrelated callbacks
 * fail loudly if accidentally reached; these are fixture boundaries only. */
long sys_pthread_canceled(void) { abort(); }
long __real_ioctl(int fd,unsigned long request,void *arg) {
    (void)fd; (void)request; (void)arg; abort();
}
struct limit { uint64_t current, maximum; };
struct usage {
    int64_t user_seconds; int32_t user_micros; uint32_t user_padding;
    int64_t system_seconds; int32_t system_micros; uint32_t system_padding;
    int64_t counters[14];
};
_Static_assert(sizeof(struct limit)==16,"Darwin rlimit16");
_Static_assert(sizeof(struct usage)==144,"Darwin rusage144");
_Static_assert(__builtin_offsetof(struct usage,counters)==32,"Darwin counters");
static int mode, calls, ins, outs, expected_resource, expected_who;
static long raw_status;
static uint64_t host_limit[2], expected_limit[2];
static int64_t host_usage[18];
static long copy_result(ssize_t n,size_t size) {
    if(n<0) return errno_linux_to_bsd(-errno);
    return (size_t)n==size?0:-14;
}
long dearust_copyin(void *local,const void *guest,size_t length) {
    ins++; assert(length==16);
    struct iovec l={local,length}, r={(void *)guest,length};
    return copy_result(process_vm_readv(getpid(),&l,1,&r,1,0),length);
}
long dearust_copyout(const void *local,void *guest,size_t length) {
    outs++; assert(length==16 || length==144);
    struct iovec l={(void *)local,length}, r={guest,length};
    return copy_result(process_vm_writev(getpid(),&l,1,&r,1,0),length);
}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr) {
    calls++;
    if(mode) {
        assert(e==0 && f==0);
        if(mode==1 || mode==2) {
            assert(nr==302 && a==0 && b==expected_resource);
            if(mode==1) {
                assert(c==0 && d!=0);
                if(raw_status>=0) memcpy((void *)d,host_limit,16);
            } else {
                assert(c!=0 && d==0);
                assert(memcmp((void *)c,expected_limit,16)==0);
            }
        } else {
            assert(mode==3 && nr==98 && a==expected_who && b!=0 && c==0 && d==0);
            if(raw_status>=0) memcpy((void *)b,host_usage,144);
        }
        return raw_status;
    }
    long result=syscall(nr,a,b,c,d,e,f);
    return result<0?-errno:result;
}
static void reset(int next) { mode=next; calls=ins=outs=0; raw_status=0; }
static void injected_limits(void) {
    const int map[]={0,1,2,3,4,9,8,6,7};
    struct { unsigned char before[7]; struct limit value; unsigned char after[9]; } out;
    for(unsigned i=0;i<9;i++) for(unsigned posix=0;posix<2;posix++) {
        unsigned id=i|(posix?0x1000:0);
        assert(rlimit_bsd_to_linux(id)==(unsigned)map[i]);
        reset(1); expected_resource=map[i];
        host_limit[0]=UINT64_C(0x100000001); host_limit[1]=UINT64_MAX;
        memset(&out,0xa5,sizeof(out));
        assert(sys_getrlimit(id,&out.value)==0 && calls==1 && outs==1 && ins==0);
        assert(out.value.current==UINT64_C(0x100000001) && out.value.maximum==INT64_MAX);
        for(size_t j=0;j<sizeof(out.before);j++) assert(out.before[j]==0xa5);
        for(size_t j=0;j<sizeof(out.after);j++) assert(out.after[j]==0xa5);
        reset(2); expected_resource=map[i];
        expected_limit[0]=UINT64_C(0x100000001); expected_limit[1]=UINT64_MAX;
        assert(sys_setrlimit(id,&out.value)==0 && calls==1 && ins==1 && outs==0);
    }
    unsigned bad[]={9,10,0x1009,0x2000,UINT_MAX};
    for(size_t i=0;i<sizeof(bad)/sizeof(*bad);i++) {
        assert(rlimit_bsd_to_linux(bad[i])==UINT_MAX);
        reset(1); assert(sys_getrlimit(bad[i],NULL)==-22 && calls==0 && outs==0);
        reset(2); assert(sys_setrlimit(bad[i],NULL)==-14 && calls==0 && ins==1);
        struct limit valid={0,INT64_MAX};
        reset(2); assert(sys_setrlimit(bad[i],&valid)==-22 && calls==0 && ins==1);
    }
    for(size_t i=0;i<3;i++) {
        long errors[]={-11,-75,-38}; long darwin[]={-35,-84,-78};
        reset(1); expected_resource=7; raw_status=errors[i]; memset(&out,0xa5,sizeof(out));
        assert(sys_getrlimit(8,&out.value)==darwin[i] && calls==1 && outs==0);
        for(size_t j=0;j<sizeof(out);j++) assert(((unsigned char *)&out)[j]==0xa5);
        reset(1); expected_resource=7; raw_status=errors[i];
        assert(sys_getrlimit(8,NULL)==darwin[i] && outs==0);
        reset(2); expected_resource=7; raw_status=errors[i];
        struct limit valid={0,INT64_MAX}; expected_limit[0]=0; expected_limit[1]=UINT64_MAX;
        assert(sys_setrlimit(8,&valid)==darwin[i] && ins==1 && calls==1);
    }
    struct limit invalids[]={{2,1},{UINT64_MAX,1},{0,UINT64_MAX},{INT64_MAX+UINT64_C(1),UINT64_MAX}};
    for(size_t i=0;i<sizeof(invalids)/sizeof(*invalids);i++) {
        reset(2); assert(sys_setrlimit(8,&invalids[i])==(i<2?-22:-84) && calls==0 && ins==1);
    }
    /* This existing full unsigned domain gate is explicit, not a claimed fix. */
    reset(1); expected_resource=7; host_limit[0]=INT64_MAX+UINT64_C(1); host_limit[1]=UINT64_MAX;
    memset(&out,0xa5,sizeof(out));
    assert(sys_getrlimit(8,&out.value)==-84 && outs==0);
    reset(1); expected_resource=7; host_limit[0]=INT64_MAX; host_limit[1]=UINT64_MAX;
    assert(sys_getrlimit(8,&out.value)==-84 && outs==0);
    reset(1); expected_resource=7; host_limit[0]=INT64_MAX+UINT64_C(1); host_limit[1]=UINT64_MAX-1;
    assert(sys_getrlimit(8,&out.value)==0 && out.value.current==host_limit[0] && out.value.maximum==host_limit[1]);
    reset(2); expected_resource=7; expected_limit[0]=host_limit[0]; expected_limit[1]=host_limit[1];
    assert(sys_setrlimit(8,&out.value)==0 && calls==1);
    struct limit reordered={INT64_MAX,INT64_MAX+UINT64_C(1)};
    reset(2); assert(sys_setrlimit(8,&reordered)==-84 && calls==0);
    reset(1); expected_resource=7; host_limit[0]=0; host_limit[1]=INT64_MAX;
    assert(sys_getrlimit(8,&out.value)==-84 && outs==0);
    reset(1); expected_resource=7; raw_status=-INT64_C(0x100000004);
    assert(sys_getrlimit(8,NULL)==-4 && outs==0);
    for(unsigned value=0;value<2;value++) {
        reset(1); expected_resource=7; host_limit[0]=value; host_limit[1]=value;
        assert(sys_getrlimit(8,&out.value)==0 && out.value.current==value && out.value.maximum==value);
    }
    reset(1); expected_resource=7; host_limit[0]=UINT64_MAX; host_limit[1]=UINT64_MAX;
    assert(sys_getdtablesize()==INT_MAX && calls==1);
}
static void injected_usage(void) {
    struct { unsigned char before[8]; struct usage value; unsigned char after[8]; } out;
    for(unsigned extreme=0;extreme<2;extreme++) {
        for(unsigned i=0;i<18;i++) host_usage[i]=extreme?INT64_MAX:-(int64_t)(i+1);
        host_usage[0]=INT64_C(0x123456789); host_usage[2]=INT64_MIN;
        host_usage[1]=extreme?INT64_MAX:123456; host_usage[3]=extreme?INT64_MIN:999999;
        for(int who=0;who>=-1;who--) {
            reset(3); expected_who=who; memset(&out,0xa5,sizeof(out));
            assert(sys_getrusage(who,&out.value)==0 && calls==1 && outs==1 && ins==0);
            assert(out.value.user_seconds==host_usage[0] && out.value.system_seconds==host_usage[2]);
            assert(out.value.user_micros==(int32_t)host_usage[1] && out.value.system_micros==(int32_t)host_usage[3]);
            assert(out.value.user_padding==0 && out.value.system_padding==0);
            for(unsigned i=4;i<18;i++) {
                uint64_t expected=(uint64_t)host_usage[i]*(i==4?1024:1);
                assert((uint64_t)out.value.counters[i-4]==expected);
            }
            for(unsigned i=0;i<8;i++) assert(out.before[i]==0xa5 && out.after[i]==0xa5);
        }
    }
    int invalid[]={1,-2,INT_MIN,INT_MAX};
    for(unsigned i=0;i<sizeof(invalid)/sizeof(*invalid);i++) {
        reset(3); assert(sys_getrusage(invalid[i],NULL)==-22 && calls==0 && outs==0);
    }
    reset(3); expected_who=0; raw_status=-11; memset(&out,0xa5,sizeof(out));
    assert(sys_getrusage(0,&out.value)==-35 && outs==0 && calls==1);
    for(size_t i=0;i<sizeof(out);i++) assert(((unsigned char *)&out)[i]==0xa5);
}
static void pointer_faults(void) {
    size_t page=(size_t)sysconf(_SC_PAGESIZE);
    unsigned char *p=mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    assert(p!=MAP_FAILED); memset(p,0xa5,page*2);
    reset(1); expected_resource=7; host_limit[0]=1; host_limit[1]=99;
    assert(sys_getrlimit(8,p+1)==0 && p[0]==0xa5 && p[17]==0xa5);
    reset(3); expected_who=0;
    assert(sys_getrusage(0,p+1)==0 && p[0]==0xa5 && p[145]==0xa5);
    struct usage unaligned; memcpy(&unaligned,p+1,sizeof(unaligned));
    assert(unaligned.user_padding==0 && unaligned.system_padding==0);
    struct limit input={1,99}; memcpy(p+1,&input,16);
    reset(2); expected_resource=7; expected_limit[0]=1; expected_limit[1]=99;
    assert(sys_setrlimit(8,p+1)==0);
    assert(mprotect(p,page,PROT_READ)==0);
    assert(sys_setrlimit(8,p+1)==0); /* read-only input is readable. */
    reset(1); expected_resource=7; assert(sys_getrlimit(8,p+1)==-14);
    reset(3); expected_who=0; assert(sys_getrusage(0,p+1)==-14);
    assert(mprotect(p,page,PROT_READ|PROT_WRITE)==0);
    assert(mprotect(p+page,page,PROT_NONE)==0);
    reset(1); expected_resource=7; assert(sys_getrlimit(8,p+page-8)==-14);
    reset(2); expected_resource=7; assert(sys_setrlimit(8,p+page-8)==-14 && calls==0);
    reset(3); expected_who=0; assert(sys_getrusage(0,p+page-72)==-14);
    reset(1); expected_resource=7; assert(sys_getrlimit(8,NULL)==-14);
    reset(2); assert(sys_setrlimit(8,NULL)==-14 && calls==0);
    reset(3); expected_who=0; assert(sys_getrusage(0,NULL)==-14);
    reset(2); assert(sys_setrlimit(8,p+page)==-14 && calls==0);
    assert(munmap(p,page*2)==0);
}
static void child_limits(void) {
    struct rlimit original; assert(getrlimit(RLIMIT_NOFILE,&original)==0);
    mode=0;
    for(unsigned desired=0;desired<3;desired++) {
        pid_t pid=fork(); assert(pid>=0);
        if(pid==0) {
            uint64_t cap=desired==2?32:desired;
            if(cap>original.rlim_max) _exit(77);
            struct limit set={cap,original.rlim_max==RLIM_INFINITY?INT64_MAX:original.rlim_max},got;
            assert(sys_setrlimit(8,&set)==0);
            assert(sys_getrlimit(8,&got)==0 && got.current==cap && got.maximum==set.maximum);
            assert(sys_getdtablesize()==(long)cap);
            struct rlimit host; assert(getrlimit(RLIMIT_NOFILE,&host)==0 && host.rlim_cur==cap);
            if(desired==2) {
                int descriptors[64], count=0;
                while(count<64) { int fd=open("/dev/null",O_RDONLY); if(fd<0) break; descriptors[count++]=fd; }
                assert(errno==EMFILE && count<64);
                assert(sys_getrlimit(8,&got)==0 && got.current==32 && sys_getdtablesize()==32);
                assert(sys_setrlimit(8,&set)==0);
                for(int i=0;i<count;i++) assert(close(descriptors[i])==0);
            }
            pid_t nested=fork(); assert(nested>=0);
            if(nested==0) { struct limit inherited; assert(sys_getrlimit(8,&inherited)==0 && inherited.current==cap); _exit(0); }
            int status; assert(waitpid(nested,&status,0)==nested && WIFEXITED(status) && WEXITSTATUS(status)==0);
            struct usage usage; assert(sys_getrusage(-1,&usage)==0 && usage.user_padding==0 && usage.system_padding==0);
            _exit(0);
        }
        int status; assert(waitpid(pid,&status,0)==pid && WIFEXITED(status));
        assert(WEXITSTATUS(status)==0 || WEXITSTATUS(status)==77);
    }
    struct rlimit after; assert(getrlimit(RLIMIT_NOFILE,&after)==0 && memcmp(&original,&after,sizeof(after))==0);
    struct limit original_view; assert(sys_getrlimit(8,&original_view)==0);
    assert(original_view.current==(original.rlim_cur==RLIM_INFINITY?INT64_MAX:original.rlim_cur));
    struct usage usage; memset(&usage,0xa5,sizeof(usage)); assert(sys_getrusage(0,&usage)==0);
    assert(usage.user_padding==0 && usage.system_padding==0 && usage.counters[0]>=0 && usage.counters[0]%1024==0);
    assert(usage.user_micros>=0 && usage.user_micros<1000000 && usage.system_micros>=0 && usage.system_micros<1000000);
}
int main(void) {
    injected_limits(); injected_usage(); pointer_faults(); child_limits();
    puts("resource native injection/fault/fork/NOFILE0/1/full-table checks passed; unsigned-domain gate remains explicit");
    return 0;
}
