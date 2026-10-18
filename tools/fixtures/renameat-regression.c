/* Real Darling dirfd/vchroot, replacement identities and thread cwd. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
extern int pthread_fchdir_np(int);
static struct stat info(int fd,const char *p) { struct stat s;assert(fstatat(fd,p,&s,AT_SYMLINK_NOFOLLOW)==0);return s; }
int main(void) {
    char temp[]="/tmp/dearust-renameat-XXXXXX";assert(mkdtemp(temp));
    char src[256],dst[256];snprintf(src,sizeof(src),"%s/src",temp);snprintf(dst,sizeof(dst),"%s/dst",temp);
    assert(mkdir(src,0700)==0&&mkdir(dst,0700)==0);int a=open(src,O_RDONLY),b=open(dst,O_RDONLY);assert(a>=0&&b>=0);assert(chdir(temp)==0);
    int fd=openat(a,"file",O_CREAT|O_WRONLY,0600);assert(fd>=0);assert(write(fd,"origin",6)==6);close(fd);
    fd=openat(b,"replace",O_CREAT|O_WRONLY,0600);assert(fd>=0);close(fd);
    ino_t ino=info(a,"file").st_ino;assert(renameat(a,"file",b,"replace")==0);assert(info(b,"replace").st_ino==ino);
    char bytes[6];fd=openat(b,"replace",O_RDONLY);assert(fd>=0&&read(fd,bytes,6)==6&&memcmp(bytes,"origin",6)==0);close(fd);
    struct stat s;errno=0;assert(fstatat(a,"file",&s,0)==-1&&errno==ENOENT);errno=0;assert(lstat("replace",&s)==-1&&errno==ENOENT);
    assert(symlinkat("missing",a,"sym")==0);ino=info(a,"sym").st_ino;assert(renameat(a,"sym",b,"sym")==0);
    assert(S_ISLNK(info(b,"sym").st_mode)&&info(b,"sym").st_ino==ino);
    assert(symlinkat("missing",a,"cwd")==0);ino=info(a,"cwd").st_ino;assert(pthread_fchdir_np(a)==0);
    assert(renameat(AT_FDCWD,"cwd",b,"thread")==0);assert(pthread_fchdir_np(-1)==0);assert(info(b,"thread").st_ino==ino);
    assert(symlinkat("missing",a,"cwd-dest")==0);ino=info(a,"cwd-dest").st_ino;assert(pthread_fchdir_np(b)==0);
    assert(renameat(a,"cwd-dest",AT_FDCWD,"thread-dest")==0);assert(pthread_fchdir_np(-1)==0);assert(info(b,"thread-dest").st_ino==ino);
    errno=0;assert(renameat(a,"missing",b,"none")==-1&&errno==ENOENT);errno=0;assert(renameat(-42,"file",b,"badfd")==-1&&errno==ENOENT);
    errno=0;assert(renameat(a,NULL,b,"none")==-1&&errno==EFAULT);errno=0;assert(renameat(a,"none",b,NULL)==-1&&errno==EFAULT);
    char longpath[4097];memset(longpath,'x',sizeof(longpath));errno=0;assert(renameat(a,longpath,b,"none")==-1&&errno==ENAMETOOLONG);
    errno=0;assert(renameat(a,"none",b,longpath)==-1&&errno==ENAMETOOLONG);longpath[4096]=0;
    errno=0;assert(renameat(a,longpath,b,"none")==-1&&errno==ENAMETOOLONG);
    assert(unlinkat(b,"replace",0)==0&&unlinkat(b,"sym",0)==0&&unlinkat(b,"thread",0)==0&&unlinkat(b,"thread-dest",0)==0);close(a);close(b);assert(chdir("/")==0);
    assert(rmdir(src)==0&&rmdir(dst)==0&&rmdir(temp)==0);puts("Rust renameat regression checks passed");return 0;
}
