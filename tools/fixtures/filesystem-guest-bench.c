/* Four filesystem statistics entries on one owned guest pathname/descriptor. */
#include <assert.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <unistd.h>
extern int pthread_fchdir_np(int);
static int directory,file;
static char root[]="/private/tmp/dearust-filesystem-bench-XXXXXX";
static uint64_t micros(void){struct timeval t;assert(gettimeofday(&t,NULL)==0);return (uint64_t)t.tv_sec*1000000+t.tv_usec;}
static uint64_t cpu(void){struct rusage r;assert(getrusage(RUSAGE_SELF,&r)==0);return ((uint64_t)r.ru_utime.tv_sec+r.ru_stime.tv_sec)*1000000+r.ru_utime.tv_usec+r.ru_stime.tv_usec;}
static void prepare(void){
    assert(mkdtemp(root));directory=open(root,O_RDONLY);assert(directory>=0 && pthread_fchdir_np(directory)==0);
    file=openat(directory,"file",O_CREAT|O_EXCL|O_RDWR,0600);assert(file>=0);
}
static void roundtrip(void){
    struct {uint64_t before;struct statfs fs;uint64_t after;} out;
    out.before=out.after=UINT64_C(0xa5a5a5a5a5a5a5a5);
    assert(syscall(SYS_statfs,"file",&out.fs)==0);uint32_t size=out.fs.f_bsize;fsid_t id=out.fs.f_fsid;
    assert(size>0 && out.fs.f_mntonname[0]=='/');
    assert(syscall(SYS_statfs64,"file",&out.fs)==0 && out.fs.f_bsize==size && memcmp(&id,&out.fs.f_fsid,sizeof(id))==0);
    assert(syscall(SYS_fstatfs,file,&out.fs)==0 && out.fs.f_bsize==size && memcmp(&id,&out.fs.f_fsid,sizeof(id))==0);
    assert(syscall(SYS_fstatfs64,file,&out.fs)==0 && out.fs.f_bsize==size && memcmp(&id,&out.fs.f_fsid,sizeof(id))==0);
    assert(out.before==UINT64_C(0xa5a5a5a5a5a5a5a5) && out.after==UINT64_C(0xa5a5a5a5a5a5a5a5));
}
static void cleanup(void){assert(close(file)==0 && unlinkat(directory,"file",0)==0 && pthread_fchdir_np(-1)==0 && close(directory)==0 && rmdir(root)==0);}
int main(void){
    alarm(120);prepare();for(unsigned i=0;i<100;i++)roundtrip();
    for(unsigned sample=0;sample<21;sample++){
        uint64_t c=cpu(),wall=micros();for(unsigned i=0;i<200;i++)roundtrip();wall=micros()-wall;c=cpu()-c;
        fprintf(stderr,"FILESYSTEM_BENCH sample=%u roundtrips=200 cpu_us=%llu wall_us=%llu\n",sample,(unsigned long long)c,(unsigned long long)wall);
    }
    cleanup();puts("Dearust filesystem statistics benchmark checks passed");return 0;
}
