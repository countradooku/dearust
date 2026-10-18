/* Actual Darwin stream ABI, including full-width byte counts and fault errors. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>
static const int reads[]={SYS_read,SYS_read_nocancel};
static const int writes[]={SYS_write,SYS_write_nocancel};
static void error(int call,int fd,void *buffer,size_t count,int expected){
    errno=0;assert(syscall(call,fd,buffer,count)==-1 && errno==expected);
}
int main(void){
    alarm(30);char path[]="/private/tmp/dearust-stream-XXXXXX";
    int fd=mkstemp(path);assert(fd>=0);int ro=open(path,O_RDONLY),wo=open(path,O_WRONLY);assert(ro>=0 && wo>=0);
    char data[64],out[64];memset(data,0x5a,sizeof(data));
    for(unsigned v=0;v<2;v++){
        assert(lseek(fd,0,SEEK_SET)==0 && syscall(writes[v],fd,data,sizeof(data))==sizeof(data));
        assert(lseek(fd,0,SEEK_SET)==0 && syscall(reads[v],fd,out,sizeof(out))==sizeof(out));
        assert(memcmp(data,out,sizeof(data))==0 && syscall(reads[v],fd,out,1)==0);
        error(reads[v],-1,out,1,EBADF);error(writes[v],-1,data,1,EBADF);
        error(reads[v],wo,out,1,EBADF);error(writes[v],ro,data,1,EBADF);
        const size_t large[]={UINT64_C(0x80000000),UINT64_C(0xffffffff),UINT64_C(0x100000000),UINT64_C(0x100000001),SIZE_MAX};
        for(unsigned n=0;n<sizeof(large)/sizeof(large[0]);n++){
            off_t at=lseek(fd,0,SEEK_CUR);assert(at>=0);
            error(reads[v],fd,(void*)1,large[n],EINVAL);error(writes[v],fd,(void*)1,large[n],EINVAL);
            error(reads[v],-1,(void*)1,large[n],EBADF);error(writes[v],-1,(void*)1,large[n],EBADF);
            error(reads[v],wo,(void*)1,large[n],EBADF);error(writes[v],ro,(void*)1,large[n],EBADF);
            assert(lseek(fd,0,SEEK_CUR)==at);
        }
        long page=sysconf(_SC_PAGESIZE);assert(page>0);
        void *blocked=mmap(NULL,(size_t)page,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);assert(blocked!=MAP_FAILED);
        assert(lseek(fd,0,SEEK_SET)==0);error(reads[v],fd,blocked,1,EFAULT);error(writes[v],fd,blocked,1,EFAULT);
        assert(munmap(blocked,(size_t)page)==0);
        int pipefd[2];assert(pipe(pipefd)==0);
        assert(syscall(writes[v],pipefd[1],data,7)==7);
        memset(out,0,sizeof(out));assert(syscall(reads[v],pipefd[0],out,sizeof(out))==7 && memcmp(out,data,7)==0);
        assert(fcntl(pipefd[0],F_SETFL,O_NONBLOCK)==0);error(reads[v],pipefd[0],out,1,EAGAIN);
        assert(close(pipefd[1])==0 && syscall(reads[v],pipefd[0],out,1)==0 && close(pipefd[0])==0);
        assert(syscall(reads[v],fd,(void*)1,0)==0 && syscall(writes[v],fd,(void*)1,0)==0);
    }
    assert(close(ro)==0 && close(wo)==0 && close(fd)==0 && unlink(path)==0);
    puts("Dearust stream regression checks passed");return 0;
}
