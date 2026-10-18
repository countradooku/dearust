/* Own-process mask changes only, restored before exit. Raw Darwin syscall path. */
#include <assert.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
int main(void) {
    mode_t original=umask(0);
    assert(syscall(SYS_umask,027)==0);
    char dir[]="/tmp/dearust-umask-XXXXXX";assert(mkdtemp(dir));
    char path[256];snprintf(path,sizeof(path),"%s/file",dir);
    int fd=open(path,O_CREAT|O_EXCL|O_WRONLY,0666);assert(fd>=0);
    struct stat st;assert(fstat(fd,&st)==0 && (st.st_mode&0777)==0640);
    assert(close(fd)==0);
    assert(syscall(SYS_umask,0xffffffffU)==027);
    assert(umask(original)==0777);
    assert(unlink(path)==0 && rmdir(dir)==0);
    puts("Rust umask regression checks passed");return 0;
}
