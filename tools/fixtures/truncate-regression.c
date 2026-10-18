/* Actual Darling sparse path truncation, offsets and per-thread cwd. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
extern int pthread_fchdir_np(int);
static int create(void){int fd=open("target",O_CREAT|O_EXCL|O_RDWR,0600);assert(fd>=0 && write(fd,"kept",4)==4 && lseek(fd,2,SEEK_SET)==2);return fd;}
int main(void){
    char root[]="/tmp/dearust-truncate-XXXXXX";assert(mkdtemp(root));
    char dest[256];snprintf(dest,sizeof(dest),"%s/dest",root);assert(mkdir(dest,0700)==0 && chdir(dest)==0);
    int fd=create(),dfd=open(dest,O_RDONLY);assert(dfd>=0 && symlink("target","link")==0);
    assert(chdir(root)==0);int unrelated=create();assert(pthread_fchdir_np(dfd)==0);
    off_t large=((off_t)1<<32)+123;assert(truncate("link",large)==0);
    struct stat st;assert(fstat(fd,&st)==0 && st.st_size==large && lseek(fd,0,SEEK_CUR)==2);
    assert(fstat(unrelated,&st)==0 && st.st_size==4);
    unsigned char bytes[6];memset(bytes,0xa5,sizeof(bytes));assert(pread(fd,bytes+1,4,0)==4 && memcmp(bytes+1,"kept",4)==0 && bytes[0]==0xa5 && bytes[5]==0xa5);
    assert(pread(fd,bytes+1,1,large-1)==1 && bytes[1]==0 && bytes[0]==0xa5);
    errno=0;assert(truncate("target",-1)==-1 && errno==EINVAL);
    errno=0;assert(truncate("target",INT64_MIN)==-1 && errno==EINVAL);
    assert(fstat(fd,&st)==0 && st.st_size==large);
    errno=0;assert(truncate("missing",0)==-1 && errno==ENOENT);
    errno=0;assert(truncate(NULL,0)==-1 && errno==EFAULT);
    char longpath[4097];memset(longpath,'x',sizeof(longpath));
    errno=0;assert(truncate(longpath,0)==-1 && errno==ENAMETOOLONG);
    longpath[4096]=0;errno=0;assert(truncate(longpath,0)==-1 && errno==ENAMETOOLONG);
    assert(truncate("target",2)==0 && fstat(fd,&st)==0 && st.st_size==2 && lseek(fd,0,SEEK_CUR)==2);
    assert(pthread_fchdir_np(-1)==0 && fstat(unrelated,&st)==0 && st.st_size==4);
    assert(chdir(dest)==0 && unlink("link")==0 && unlink("target")==0 && chdir(root)==0 && unlink("target")==0);
    assert(close(fd)==0 && close(unrelated)==0 && close(dfd)==0 && chdir("/")==0 && rmdir(dest)==0 && rmdir(root)==0);
    puts("Rust truncate regression checks passed");return 0;
}
