/* Actual Darling independent-vs-shared descriptor lock ownership. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <unistd.h>
int main(void) {
    char name[]="/tmp/dearust-flock-XXXXXX"; int fd=mkstemp(name); assert(fd>=0);
    assert(write(fd,"kept",4)==4 && lseek(fd,2,SEEK_SET)==2);
    int duplicate=dup(fd), other=open(name,O_RDWR); assert(duplicate>=0 && other>=0);
    assert(flock(fd,LOCK_EX|LOCK_NB)==0 && flock(duplicate,LOCK_EX|LOCK_NB)==0);
    errno=0; assert(flock(other,LOCK_EX|LOCK_NB)==-1 && errno==EWOULDBLOCK);
    errno=0; assert(flock(other,LOCK_SH|LOCK_NB)==-1 && errno==EWOULDBLOCK);
    assert(flock(duplicate,LOCK_UN)==0 && flock(other,LOCK_SH|LOCK_NB)==0);
    assert(flock(fd,LOCK_SH|LOCK_NB)==0);
    assert(flock(fd,LOCK_UN)==0 && flock(other,LOCK_UN)==0);
    assert(flock(fd,LOCK_EX|LOCK_NB)==0 && close(fd)==0);
    errno=0; assert(flock(other,LOCK_EX|LOCK_NB)==-1 && errno==EWOULDBLOCK);
    assert(close(duplicate)==0 && flock(other,LOCK_EX|LOCK_NB)==0 && flock(other,LOCK_UN)==0);
    errno=0; assert(flock(other,0)==-1 && errno==EINVAL);
    errno=0; assert(flock(-42,LOCK_EX|LOCK_NB)==-1 && errno==EBADF);
    unsigned char data[6]; memset(data,0xa5,sizeof(data));
    assert(pread(other,data+1,4,0)==4 && memcmp(data+1,"kept",4)==0 && data[0]==0xa5 && data[5]==0xa5);
    assert(lseek(other,0,SEEK_CUR)==0 && close(other)==0 && unlink(name)==0);
    puts("Rust flock regression checks passed");
}
