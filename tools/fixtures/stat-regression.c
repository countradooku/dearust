/* Actual Darwin stat/stat64 fields and per-thread working-directory handling. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
extern int pthread_fchdir_np(int);
struct narrow {unsigned char head[16];struct stat value;unsigned char tail[16];};
struct wide {unsigned char head[16];struct stat64 value;unsigned char tail[16];};
int main(void){
    char root[]="/tmp/dearust-stat-XXXXXX";assert(mkdtemp(root));int dfd=open(root,O_RDONLY);assert(dfd>=0);
    int fd=openat(dfd,"file",O_CREAT|O_EXCL|O_RDWR,0640);assert(fd>=0 && write(fd,"payload",7)==7);
    char link[256],dangling[256];assert(snprintf(link,sizeof(link),"%s/link",root)>0 && snprintf(dangling,sizeof(dangling),"%s/dangling",root)>0);
    assert(symlink("file",link)==0 && symlink("missing",dangling)==0);
    struct stat ref;struct stat64 refwide;assert(fstat(fd,&ref)==0 && fstat64(fd,&refwide)==0);
    assert(chdir("/")==0 && pthread_fchdir_np(dfd)==0);
    struct narrow n,nb;struct wide w,wb;memset(&n,0xa5,sizeof(n));memset(&w,0xa5,sizeof(w));
    assert(stat("link",&n.value)==0 && stat64("link",&w.value)==0);
    assert(S_ISREG(n.value.st_mode) && S_ISREG(w.value.st_mode));
    assert(n.value.st_size==7 && w.value.st_size==7 && n.value.st_ino==ref.st_ino && w.value.st_ino==refwide.st_ino);
    assert(n.value.st_dev==ref.st_dev && w.value.st_dev==ref.st_dev && n.value.st_uid==ref.st_uid && w.value.st_uid==ref.st_uid);
    assert(n.value.st_gid==ref.st_gid && w.value.st_gid==ref.st_gid && n.value.st_nlink==ref.st_nlink && w.value.st_nlink==ref.st_nlink);
    for(size_t i=0;i<sizeof(n.head);i++)assert(n.head[i]==0xa5 && n.tail[i]==0xa5 && w.head[i]==0xa5 && w.tail[i]==0xa5);
    nb=n;wb=w;
#define ERROR_CASE(path,error) do {errno=0;assert(stat(path,&n.value)==-1 && errno==error);errno=0;assert(stat64(path,&w.value)==-1 && errno==error);assert(memcmp(&n,&nb,sizeof(n))==0 && memcmp(&w,&wb,sizeof(w))==0);} while(0)
    ERROR_CASE("missing",ENOENT);ERROR_CASE("dangling",ENOENT);ERROR_CASE(NULL,EFAULT);
    char longpath[4097];memset(longpath,'x',sizeof(longpath));ERROR_CASE(longpath,ENAMETOOLONG);longpath[4096]=0;ERROR_CASE(longpath,ENAMETOOLONG);
    errno=0;assert(stat("file",NULL)==-1 && errno==EFAULT);errno=0;assert(stat64("file",NULL)==-1 && errno==EFAULT);
    assert(pthread_fchdir_np(-1)==0 && stat(link,&n.value)==0 && stat64(link,&w.value)==0);
    assert(close(fd)==0 && unlink(link)==0 && unlink(dangling)==0 && unlinkat(dfd,"file",0)==0);
    assert(close(dfd)==0 && rmdir(root)==0);
    puts("Rust stat regression checks passed");
}
