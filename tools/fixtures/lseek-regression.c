/* Actual Darling signed 64-bit offsets and shared open-file descriptions. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
int main(void) {
    char name[]="/tmp/dearust-lseek-XXXXXX"; int fd=mkstemp(name); assert(fd>=0);
    assert(write(fd,"kept",4)==4 && lseek(fd,2,SEEK_SET)==2);
    off_t large=(INT64_C(1)<<32)+123;
    assert(ftruncate(fd,large)==0 && lseek(fd,0,SEEK_CUR)==2);
    assert(lseek(fd,0,SEEK_END)==large && lseek(fd,-1,SEEK_END)==large-1);
    assert(lseek(fd,1,SEEK_CUR)==large);
    unsigned char data[3]={0xa5,0xa5,0xa5};
    assert(pread(fd,data+1,1,large-1)==1 && data[0]==0xa5 && data[1]==0 && data[2]==0xa5);
    int duplicate=dup(fd), separate=open(name,O_RDONLY); assert(duplicate>=0 && separate>=0);
    assert(lseek(fd,3,SEEK_SET)==3 && lseek(duplicate,0,SEEK_CUR)==3 && lseek(separate,0,SEEK_CUR)==0);
    assert(lseek(duplicate,1,SEEK_CUR)==4 && lseek(fd,0,SEEK_CUR)==4);
    errno=0; assert(lseek(fd,-1,SEEK_SET)==-1 && errno==EINVAL);
    errno=0; assert(lseek(fd,0,-1)==-1 && errno==EINVAL);
    errno=0; assert(lseek(fd,INT64_MIN,SEEK_SET)==-1 && errno==EINVAL);
    errno=0; assert(lseek(-42,0,SEEK_SET)==-1 && errno==EBADF);
    assert(lseek(fd,0,SEEK_CUR)==4);
    int pipefd[2]; assert(pipe(pipefd)==0);
    errno=0; assert(lseek(pipefd[0],0,SEEK_CUR)==-1 && errno==ESPIPE);
    assert(close(pipefd[0])==0 && close(pipefd[1])==0);
    assert(close(duplicate)==0 && close(separate)==0 && close(fd)==0 && unlink(name)==0);
    puts("Rust lseek regression checks passed");
}
