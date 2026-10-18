/* Actual Darwin stat/stat64 layout, sparse metadata and NULL/error precedence. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
extern int fstat64(int,struct stat64 *);
extern int stat64(const char *,struct stat64 *);
struct narrow {unsigned char head[32];struct stat value;unsigned char tail[32];};
struct wide {unsigned char head[32];struct stat64 value;unsigned char tail[32];};
static void guards(const unsigned char *head,const unsigned char *tail){for(unsigned i=0;i<32;i++)assert(head[i]==0xa5 && tail[i]==0xa5);}
int main(void){
    char path[]="/tmp/dearust-fstat-XXXXXX";int fd=mkstemp(path);assert(fd>=0 && write(fd,"kept",4)==4 && lseek(fd,2,SEEK_SET)==2);
    int duplicate=dup(fd);assert(duplicate>=0);
    struct stat reference;struct stat64 reference64;assert(stat(path,&reference)==0 && stat64(path,&reference64)==0);
    struct narrow n,nb;struct wide w,wb;memset(&n,0xa5,sizeof(n));memset(&w,0xa5,sizeof(w));
    assert(fstat(fd,&n.value)==0 && fstat64(fd,&w.value)==0);
    assert(n.value.st_ino==reference.st_ino && w.value.st_ino==reference64.st_ino);
    assert(n.value.st_size==4 && w.value.st_size==4 && S_ISREG(n.value.st_mode) && S_ISREG(w.value.st_mode));
    assert(fstat(duplicate,&n.value)==0 && fstat64(duplicate,&w.value)==0 && n.value.st_ino==reference.st_ino && w.value.st_ino==reference64.st_ino);
    off_t large=((off_t)1<<32)+123;assert(ftruncate(fd,large)==0 && fstat(duplicate,&n.value)==0 && fstat64(duplicate,&w.value)==0);
    assert(n.value.st_size==large && w.value.st_size==large && lseek(fd,0,SEEK_CUR)==2 && lseek(duplicate,0,SEEK_CUR)==2);
    unsigned char bytes[6];memset(bytes,0xa5,sizeof(bytes));assert(pread(fd,bytes+1,4,0)==4 && memcmp(bytes+1,"kept",4)==0 && bytes[0]==0xa5 && bytes[5]==0xa5);
    assert(pread(fd,bytes+1,1,large-1)==1 && bytes[1]==0 && bytes[0]==0xa5);
    guards(n.head,n.tail);guards(w.head,w.tail);nb=n;wb=w;
    errno=0;assert(fstat(-1,&n.value)==-1 && errno==EBADF);errno=0;assert(fstat64(-1,&w.value)==-1 && errno==EBADF);
    assert(memcmp(&n,&nb,sizeof(n))==0 && memcmp(&w,&wb,sizeof(w))==0);
    errno=0;assert(fstat(-1,NULL)==-1 && errno==EBADF);errno=0;assert(fstat64(-1,NULL)==-1 && errno==EBADF);
    errno=0;assert(fstat(fd,NULL)==-1 && errno==EFAULT);errno=0;assert(fstat64(fd,NULL)==-1 && errno==EFAULT);
    assert(unlink(path)==0 && fstat(fd,&n.value)==0 && fstat64(fd,&w.value)==0 && n.value.st_size==large && w.value.st_size==large);
    guards(n.head,n.tail);guards(w.head,w.tail);
    assert(close(duplicate)==0 && close(fd)==0);
    puts("Rust fstat regression checks passed");return 0;
}
