/* Actual Darling per-thread cwd and final-symlink directory removal. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
extern int pthread_fchdir_np(int);
static void directory_exists(const char *path){struct stat st;assert(stat(path,&st)==0 && S_ISDIR(st.st_mode));}
int main(void){
    char root[]="/tmp/dearust-rmdir-XXXXXX";assert(mkdtemp(root));
    char dest[256];snprintf(dest,sizeof(dest),"%s/dest",root);assert(mkdir(dest,0700)==0 && chdir(dest)==0);
    assert(mkdir("victim",0700)==0 && mkdir("target",0700)==0 && symlink("target","link")==0);
    int dfd=open(dest,O_RDONLY);assert(dfd>=0 && chdir(root)==0 && mkdir("victim",0700)==0);
    assert(pthread_fchdir_np(dfd)==0 && rmdir("victim")==0);
    errno=0;assert(rmdir("link")==-1 && errno==ENOTDIR);
    errno=0;assert(rmdir("missing")==-1 && errno==ENOENT);
    errno=0;assert(rmdir(NULL)==-1 && errno==EFAULT);
    char longpath[4097];memset(longpath,'x',sizeof(longpath));
    errno=0;assert(rmdir(longpath)==-1 && errno==ENAMETOOLONG);
    longpath[4096]=0;errno=0;assert(rmdir(longpath)==-1 && errno==ENAMETOOLONG);
    assert(pthread_fchdir_np(-1)==0);directory_exists("victim");
    assert(chdir(dest)==0);directory_exists("target");struct stat st;assert(lstat("link",&st)==0 && S_ISLNK(st.st_mode));
    errno=0;assert(lstat("victim",&st)==-1 && errno==ENOENT);
    assert(unlink("link")==0 && rmdir("target")==0 && chdir(root)==0 && rmdir("victim")==0);
    assert(close(dfd)==0 && chdir("/")==0 && rmdir(dest)==0 && rmdir(root)==0);
    puts("Rust rmdir regression checks passed");return 0;
}
