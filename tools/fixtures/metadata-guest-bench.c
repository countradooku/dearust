/* Raw legacy SYS_stat/SYS_fstat require the SDK's pre-inode64 struct stat.
 * Default modern SDK stat maps to the stat64 layout and is incompatible here. */
#define _DARWIN_NO_64_BIT_INODE 1
#include <assert.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <unistd.h>
_Static_assert(__DARWIN_64_BIT_INO_T==0,"raw legacy stat needs legacy inode layout");
_Static_assert(sizeof(struct stat)==120,"Darwin x86_64 legacy stat size");
_Static_assert(__builtin_offsetof(struct stat,st_mode)==8,"legacy stat mode offset");
_Static_assert(__builtin_offsetof(struct stat,st_size)==72,"legacy stat size offset");
_Static_assert(sizeof(((struct stat *)0)->st_ino)==4,"legacy stat inode width");
_Static_assert(sizeof(struct stat64)==144,"Darwin x86_64 stat64 size");
_Static_assert(__builtin_offsetof(struct stat64,st_mode)==4,"stat64 mode offset");
_Static_assert(__builtin_offsetof(struct stat64,st_size)==96,"stat64 size offset");
_Static_assert(sizeof(((struct stat64 *)0)->st_ino)==8,"stat64 inode width");
struct narrow {unsigned char head[16];struct stat value;unsigned char tail[16];};
struct wide {unsigned char head[16];struct stat64 value;unsigned char tail[16];};
static uint64_t micros(void){struct timeval t;assert(gettimeofday(&t,NULL)==0);return (uint64_t)t.tv_sec*1000000+t.tv_usec;}
static uint64_t cpu(void){struct rusage r;assert(getrusage(RUSAGE_SELF,&r)==0);return ((uint64_t)r.ru_utime.tv_sec+r.ru_stime.tv_sec)*1000000+r.ru_utime.tv_usec+r.ru_stime.tv_usec;}
static void roundtrip(int fd,const char *path,struct narrow n[2],struct wide w[2]){
    assert(syscall(SYS_fstat,fd,&n[0].value)==0);assert(syscall(SYS_fstat64,fd,&w[0].value)==0);
    assert(syscall(SYS_stat,path,&n[1].value)==0);assert(syscall(SYS_stat64,path,&w[1].value)==0);
}
static void check_metadata(int fd,const struct narrow n[2],const struct wide w[2]){
    struct stat ref;struct stat64 ref64;assert(fstat(fd,&ref)==0 && fstat64(fd,&ref64)==0);
    for(unsigned i=0;i<2;i++){
        assert(S_ISREG(n[i].value.st_mode) && S_ISREG(w[i].value.st_mode));
        assert(n[i].value.st_size==7 && w[i].value.st_size==7 && n[i].value.st_ino==ref.st_ino && w[i].value.st_ino==ref64.st_ino);
        assert(n[i].value.st_dev==ref.st_dev && w[i].value.st_dev==ref64.st_dev);
        assert(n[i].value.st_uid==ref.st_uid && w[i].value.st_uid==ref64.st_uid && n[i].value.st_gid==ref.st_gid && w[i].value.st_gid==ref64.st_gid);
        for(unsigned j=0;j<16;j++)assert(n[i].head[j]==0xa5 && n[i].tail[j]==0xa5 && w[i].head[j]==0xa5 && w[i].tail[j]==0xa5);
    }
}
int main(void){
    alarm(60);char path[]="/private/tmp/dearust-metadata-bench-XXXXXX";int fd=mkstemp(path);assert(fd>=0 && write(fd,"payload",7)==7);
    struct narrow n[2];struct wide w[2];memset(n,0xa5,sizeof(n));memset(w,0xa5,sizeof(w));
    for(unsigned i=0;i<100;i++)roundtrip(fd,path,n,w);
    check_metadata(fd,n,w);
#ifndef METADATA_VERIFY_ONLY
    /* 2000 four-call rounds/sample keep this metadata workload comfortably bounded. */
    for(unsigned sample=0;sample<21;sample++){
        uint64_t c=cpu(),wall=micros();for(unsigned i=0;i<2000;i++)roundtrip(fd,path,n,w);wall=micros()-wall;c=cpu()-c;
        fprintf(stderr,"METADATA_BENCH sample=%u roundtrips=2000 cpu_us=%llu wall_us=%llu\n",sample,(unsigned long long)c,(unsigned long long)wall);
    }
#endif
    check_metadata(fd,n,w);
    assert(close(fd)==0 && unlink(path)==0);
    puts("Dearust guest metadata benchmark checks passed");return 0;
}
