/* Darwin traditional pathname entries, actual thread cwd and readlinkat ABI. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
extern int pthread_fchdir_np(int);
struct context{int fd;};
static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t condition=PTHREAD_COND_INITIALIZER;
static unsigned ready;
static void *worker(void *arg){
    int fd=((struct context*)arg)->fd;assert(pthread_fchdir_np(fd)==0);
    assert(pthread_mutex_lock(&lock)==0);ready++;assert(pthread_cond_broadcast(&condition)==0);
    while(ready<2)assert(pthread_cond_wait(&condition,&lock)==0);
    assert(pthread_mutex_unlock(&lock)==0);
    for(unsigned i=0;i<16;i++){
        assert(syscall(SYS_mkdir,"sub",0700)==0);
        assert(syscall(SYS_symlink,"seed","soft")==0);
        char bytes[16];memset(bytes,0xa5,sizeof(bytes));
        assert(syscall(SYS_readlink,"soft",bytes,3)==3 && memcmp(bytes,"see",3)==0 && (unsigned char)bytes[3]==0xa5);
        assert(syscall(SYS_readlink,"soft",(void*)1,0)==0);
        assert(syscall(SYS_link,"seed","hard")==0);
        struct stat a,b;assert(fstatat(fd,"seed",&a,0)==0 && fstatat(fd,"hard",&b,0)==0 && a.st_ino==b.st_ino && a.st_nlink==2);
        assert(syscall(SYS_rename,"hard","moved")==0 && syscall(SYS_unlink,"moved")==0);
        assert(syscall(SYS_unlink,"soft")==0 && rmdir("sub")==0);
    }
    assert(pthread_fchdir_np(-1)==0);return NULL;
}
int main(void){
    alarm(60);char root[]="/private/tmp/dearust-frontends-XXXXXX";assert(mkdtemp(root));
    char before[4096],after[4096];assert(getcwd(before,sizeof(before)));
    struct context c[2];pthread_t threads[2];char paths[2][256];
    for(unsigned i=0;i<2;i++){
        assert(snprintf(paths[i],sizeof(paths[i]),"%s/d%u",root,i)>0 && mkdir(paths[i],0700)==0);
        c[i].fd=open(paths[i],O_RDONLY);assert(c[i].fd>=0);
        int seed=openat(c[i].fd,"seed",O_CREAT|O_EXCL|O_RDWR,0600);assert(seed>=0 && write(seed,"kept",4)==4 && close(seed)==0);
        assert(pthread_create(&threads[i],NULL,worker,&c[i])==0);
    }
    for(unsigned i=0;i<2;i++)assert(pthread_join(threads[i],NULL)==0);
    assert(getcwd(after,sizeof(after)) && strcmp(before,after)==0);
    assert(symlinkat("seed",c[0].fd,"soft")==0);
    unsigned char bytes[16];memset(bytes,0xa5,sizeof(bytes));
    assert(syscall(SYS_readlinkat,c[0].fd,"soft",bytes,0)==0 && bytes[0]==0xa5);
    assert(syscall(SYS_readlinkat,c[0].fd,"soft",NULL,0)==0);
    const size_t too_big[]={UINT64_C(0x80000000),UINT64_C(0x100000000),UINT64_C(0x100000001),SIZE_MAX};
    for(unsigned i=0;i<sizeof(too_big)/sizeof(too_big[0]);i++){
        errno=0;assert(syscall(SYS_readlinkat,c[0].fd,"soft",bytes,too_big[i])==-1 && errno==EINVAL);
        errno=0;assert(syscall(SYS_readlinkat,-1,NULL,NULL,too_big[i])==-1 && errno==EINVAL);
    }
    errno=0;assert(syscall(SYS_readlinkat,c[0].fd,"missing",NULL,0)==-1 && errno==ENOENT);
    errno=0;assert(syscall(SYS_readlinkat,c[0].fd,"seed",NULL,0)==-1 && errno==EINVAL);
    long page=sysconf(_SC_PAGESIZE);assert(page>0);void *blocked=mmap(NULL,(size_t)page,PROT_NONE,MAP_PRIVATE|MAP_ANON,-1,0);assert(blocked!=MAP_FAILED);
    assert(syscall(SYS_readlinkat,c[0].fd,"soft",blocked,0)==0);
    errno=0;assert(syscall(SYS_readlinkat,c[0].fd,"soft",blocked,1)==-1 && errno==EFAULT);
    assert(munmap(blocked,(size_t)page)==0 && unlinkat(c[0].fd,"soft",0)==0);
    for(unsigned i=0;i<2;i++)assert(unlinkat(c[i].fd,"seed",0)==0 && close(c[i].fd)==0 && rmdir(paths[i])==0);
    assert(rmdir(root)==0);puts("Dearust traditional pathname regression checks passed");return 0;
}
