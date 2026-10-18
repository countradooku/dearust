/* SPDX-License-Identifier: GPL-3.0-or-later
 * Native resource benchmark; full production Rust copy boundary when linked.
 * The original C variant executes the same resource calls through the same raw
 * syscall/errno adapter. Benchmark processes own their unchanged-limit writes. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <ucontext.h>
#include <unistd.h>
extern long sys_getrlimit(unsigned,void *),sys_setrlimit(unsigned,const void *),sys_getrusage(int,void *);
static int fallback;
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr) {
    long result=syscall(nr,a,b,c,d,e,f);return result<0?-errno:result;
}
#ifndef RESOURCE_BENCH_ORIGINAL
extern unsigned long dearust_copy_signal_fixup(unsigned long,int,int);
int dearust_copy_signals_ready(void) { return !fallback; }
static void fault(int signal,siginfo_t *info,void *opaque) {
    ucontext_t *context=opaque;
    unsigned long next=dearust_copy_signal_fixup((unsigned long)context->uc_mcontext.gregs[REG_RIP],signal,info->si_code);
    if(!next) _exit(128+signal);
    context->uc_mcontext.gregs[REG_RIP]=(greg_t)next;
}
static void install(void) {
    struct sigaction action;memset(&action,0,sizeof(action));action.sa_sigaction=fault;action.sa_flags=SA_SIGINFO;
    sigemptyset(&action.sa_mask);assert(sigaction(SIGSEGV,&action,NULL)==0 && sigaction(SIGBUS,&action,NULL)==0);
}
#else
static void install(void) { }
#endif
static volatile uint64_t consume;
#ifdef RESOURCE_BENCH_CHANGING
static unsigned changing_tick;
#endif
static uint64_t ns(clockid_t which) { struct timespec t;assert(clock_gettime(which,&t)==0);return (uint64_t)t.tv_sec*1000000000+t.tv_nsec; }
static void roundtrip(uint64_t limits[22],uint64_t usage[18]) {
    /* BSD_CORE4 avoids the original obsolete NOFILE off-by-one policy, while
     * each variant roundtrips its own infinity encoding without changing host. */
    assert(sys_getrlimit(4,limits)==0);
#ifdef RESOURCE_BENCH_CHANGING
    /* This benchmark process owns the only mutations. Alternate two finite
     * values without altering the hard cap or any launcher/parent limit. */
    assert(limits[1]>=1);limits[0]=(changing_tick++ & 1);
#endif
    assert(sys_setrlimit(4,limits)==0);
    assert(sys_getrusage(0,usage)==0);
    consume^=limits[0]^usage[0]^usage[4];
}
static int compare(const void *a,const void *b) {uint64_t x=*(const uint64_t *)a,y=*(const uint64_t *)b;return (x>y)-(x<y);}
int main(int argc,char **argv) {
    assert(argc==3 || argc==4);unsigned n=(unsigned)strtoul(argv[1],NULL,10);assert(n>=256);
    fallback=strcmp(argv[2],"fallback")==0;install();
    uint64_t stack_limits[22]={0},stack_usage[18]={0};
    uint64_t *limits=stack_limits,*usage=stack_usage;void *allocation=NULL;
    const char *layout=argc==4?argv[3]:"stack";
    if(strcmp(layout,"heap")==0){
        assert(posix_memalign(&allocation,4096,8192)==0);memset(allocation,0,8192);
        limits=allocation;usage=(uint64_t *)((unsigned char *)allocation+4096);
    }else if(strcmp(layout,"crosspage")==0){
        allocation=mmap(NULL,16384,PROT_READ|PROT_WRITE,MAP_ANONYMOUS|MAP_PRIVATE,-1,0);
        assert(allocation!=MAP_FAILED);memset(allocation,0,16384);
        limits=(uint64_t *)((unsigned char *)allocation+4096-8);
        usage=(uint64_t *)((unsigned char *)allocation+12288-72);
    }else assert(strcmp(layout,"stack")==0);
    uint64_t cold=ns(CLOCK_MONOTONIC);roundtrip(limits,usage);cold=ns(CLOCK_MONOTONIC)-cold;
    for(unsigned i=0;i<200;i++)roundtrip(limits,usage);
    unsigned count=0;uint64_t *latency=calloc(n/256+1,sizeof(*latency));assert(latency);
    uint64_t cpu=ns(CLOCK_THREAD_CPUTIME_ID),wall=ns(CLOCK_MONOTONIC);
    for(unsigned i=0;i<n;i++) {
        if((i%256)==0) {uint64_t begin=ns(CLOCK_MONOTONIC);roundtrip(limits,usage);latency[count++]=ns(CLOCK_MONOTONIC)-begin;}
        else roundtrip(limits,usage);
    }
    wall=ns(CLOCK_MONOTONIC)-wall;cpu=ns(CLOCK_THREAD_CPUTIME_ID)-cpu;
    qsort(latency,count,sizeof(*latency),compare);
    struct rusage metrics;assert(getrusage(RUSAGE_SELF,&metrics)==0);
    long rss=-1,private_dirty=-1;char line[256];
    FILE *memory=fopen("/proc/self/smaps_rollup","r");assert(memory);
    while(fgets(line,sizeof(line),memory)) {
        long value;
        if(sscanf(line,"Rss: %ld kB",&value)==1)rss=value;
        if(sscanf(line,"Private_Dirty: %ld kB",&value)==1)private_dirty=value;
    }
    assert(!ferror(memory) && fclose(memory)==0 && rss>=0 && private_dirty>=0);
    printf("%u %llu %llu %llu %llu %llu %llu %ld %ld %ld %ld %ld\n",n,(unsigned long long)cpu,(unsigned long long)wall,
        (unsigned long long)cold,(unsigned long long)latency[count/2],(unsigned long long)latency[(count-1)*95/100],
        (unsigned long long)latency[(count-1)*99/100],metrics.ru_minflt,metrics.ru_majflt,metrics.ru_maxrss,rss,private_dirty);
    free(latency);
    if(strcmp(layout,"heap")==0)free(allocation);
    if(strcmp(layout,"crosspage")==0)assert(munmap(allocation,16384)==0);
    return 0;
}
