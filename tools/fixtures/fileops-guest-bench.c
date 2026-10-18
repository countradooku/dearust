/* Actual guest fileops CPU samples; timings are not correctness goldens. */
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
static void roundtrip(int a,int b){assert(linkat(a,"source",b,"hard",0)==0);assert(renameat(b,"hard",a,"moved")==0);assert(unlinkat(a,"moved",0)==0);}
int main(void){
    alarm(60);char temp[]="/private/tmp/dearust-fileops-bench-XXXXXX";assert(mkdtemp(temp));
    int a=open(temp,O_RDONLY|O_DIRECTORY);assert(a>=0);assert(mkdirat(a,"dest",0700)==0);
    int b=openat(a,"dest",O_RDONLY|O_DIRECTORY);assert(b>=0);int fd=openat(a,"source",O_CREAT|O_EXCL|O_WRONLY,0600);assert(fd>=0&&close(fd)==0);
    for(unsigned i=0;i<100;i++)roundtrip(a,b);
    for(unsigned sample=0;sample<21;sample++){
        uint64_t c=cpu(),w=micros();for(unsigned i=0;i<200;i++)roundtrip(a,b);w=micros()-w;c=cpu()-c;
        fprintf(stderr,"FILEOPS_BENCH sample=%u roundtrips=200 cpu_us=%llu wall_us=%llu\n",sample,(unsigned long long)c,(unsigned long long)w);
    }
    struct stat st;assert(fstatat(a,"source",&st,0)==0&&st.st_nlink==1);
    assert(unlinkat(a,"source",0)==0&&close(b)==0&&unlinkat(a,"dest",AT_REMOVEDIR)==0&&close(a)==0&&rmdir(temp)==0);
    puts("Dearust guest fileops benchmark checks passed");return 0;
}
