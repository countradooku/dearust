/* Real Darling dirfd/vchroot, final-symlink policy and thread cwd. */
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
    char temp[]="/tmp/dearust-linkat-XXXXXX";assert(mkdtemp(temp));
    char src[256],dst[256];snprintf(src,sizeof(src),"%s/src",temp);snprintf(dst,sizeof(dst),"%s/dst",temp);
    assert(mkdir(src,0700)==0 && mkdir(dst,0700)==0);
    int a=open(src,O_RDONLY),b=open(dst,O_RDONLY);assert(a>=0&&b>=0);assert(chdir(temp)==0);
    int fd=openat(a,"file",O_CREAT|O_WRONLY,0600);assert(fd>=0);close(fd);assert(symlinkat("file",a,"sym")==0);
    assert(linkat(a,"file",b,"hard",0)==0);assert(info(a,"file").st_ino==info(b,"hard").st_ino);
    assert(linkat(a,"sym",b,"nofollow",0)==0);assert(S_ISLNK(info(b,"nofollow").st_mode));
    assert(info(a,"sym").st_ino==info(b,"nofollow").st_ino);
    assert(linkat(a,"sym",b,"follow",AT_SYMLINK_FOLLOW)==0);assert(info(a,"file").st_ino==info(b,"follow").st_ino);
    struct stat s;errno=0;assert(lstat("hard",&s)==-1&&errno==ENOENT);
    errno=0;assert(linkat(a,"file",b,"hard",0)==-1&&errno==EEXIST);
    errno=0;assert(linkat(a,"file",b,"invalid",0x80000000)==-1&&errno==EINVAL);
    /* Legacy vchroot rejects missing /proc/self/fd before the raw syscall. */
    errno=0;assert(linkat(-42,"file",b,"badfd",0)==-1&&errno==ENOENT);
    assert(pthread_fchdir_np(a)==0);assert(linkat(AT_FDCWD,"file",b,"thread",0)==0);assert(pthread_fchdir_np(-1)==0);
    assert(info(a,"file").st_ino==info(b,"thread").st_ino);
    assert(pthread_fchdir_np(b)==0);assert(linkat(a,"file",AT_FDCWD,"thread-dest",0)==0);assert(pthread_fchdir_np(-1)==0);
    assert(info(a,"file").st_ino==info(b,"thread-dest").st_ino);
    errno=0;assert(linkat(a,NULL,b,"none",0)==-1&&errno==EFAULT);
    errno=0;assert(linkat(a,"file",b,NULL,0)==-1&&errno==EFAULT);
    char longpath[4097];memset(longpath,'x',sizeof(longpath));
    errno=0;assert(linkat(a,longpath,b,"none",0)==-1&&errno==ENAMETOOLONG);
    errno=0;assert(linkat(a,"file",b,longpath,0)==-1&&errno==ENAMETOOLONG);
    longpath[4096]=0;errno=0;assert(linkat(a,longpath,b,"none",0)==-1&&errno==ENAMETOOLONG);
    const char *names[]={"hard","nofollow","follow","thread","thread-dest"};for(unsigned i=0;i<5;i++)assert(unlinkat(b,names[i],0)==0);
    assert(unlinkat(a,"sym",0)==0&&unlinkat(a,"file",0)==0);close(a);close(b);assert(chdir("/")==0);assert(rmdir(src)==0&&rmdir(dst)==0&&rmdir(temp)==0);
    puts("Rust linkat regression checks passed");return 0;
}
