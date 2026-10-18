/* Actual guest absolute paths; raw CPU/wall measurements are not goldens. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>
static uint64_t micros(void){struct timeval t;assert(gettimeofday(&t,NULL)==0);return (uint64_t)t.tv_sec*1000000+t.tv_usec;}
static uint64_t cpu(void){struct rusage r;assert(getrusage(RUSAGE_SELF,&r)==0);return ((uint64_t)r.ru_utime.tv_sec+r.ru_stime.tv_sec)*1000000+r.ru_utime.tv_usec+r.ru_stime.tv_usec;}
static void roundtrip(const char *directory,const char *file){assert(mkdirat(AT_FDCWD,directory,0700)==0);assert(rmdir(directory)==0);assert(truncate(file,128)==0);}
int main(void){
    alarm(60);char root[]="/private/tmp/dearust-paths-bench-XXXXXX";assert(mkdtemp(root));
    char directory[256],file[256];assert(snprintf(directory,sizeof(directory),"%s/entry",root)>0 && snprintf(file,sizeof(file),"%s/file",root)>0);
    int fd=open(file,O_CREAT|O_EXCL|O_RDWR,0600);assert(fd>=0 && write(fd,"kept",4)==4 && lseek(fd,2,SEEK_SET)==2);
    for(unsigned i=0;i<100;i++)roundtrip(directory,file);
    for(unsigned sample=0;sample<21;sample++){
        uint64_t c=cpu(),w=micros();for(unsigned i=0;i<200;i++)roundtrip(directory,file);w=micros()-w;c=cpu()-c;
        fprintf(stderr,"PATHS_BENCH sample=%u roundtrips=200 cpu_us=%llu wall_us=%llu\n",sample,(unsigned long long)c,(unsigned long long)w);
    }
    struct stat st;assert(fstat(fd,&st)==0 && st.st_size==128 && lseek(fd,0,SEEK_CUR)==2);
    char data[4];assert(pread(fd,data,sizeof(data),0)==sizeof(data) && memcmp(data,"kept",4)==0);
    errno=0;assert(lstat(directory,&st)==-1 && errno==ENOENT);
    assert(close(fd)==0 && unlink(file)==0 && rmdir(root)==0);
    puts("Dearust guest paths benchmark checks passed");return 0;
}
