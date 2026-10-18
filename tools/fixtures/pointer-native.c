/* Native pointer boundary, kernel mapping permissions and fork checks.
 * Converters intentionally use opaque 120/144-byte guest regions, not a fake
 * Darwin field layout. Real layouts are covered by pointer-regression.c. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
#include "darwin-stat-native.h"
struct expanded{char path[4096];unsigned flags;int dfd;};
extern long sys_faccessat(int,const char *,int,int),sys_fchmodat(int,const char *,int,int);
extern int do_linux_fchmodat(int,const char *,int,int);
extern long sys_access(const char *,int),sys_chmod(const char *,int);
extern long sys_linkat(int,const char *,int,const char *,int),sys_renameat(int,const char *,int,const char *);
extern long sys_unlinkat(int,const char *,int),sys_mkdirat(int,const char *,int),sys_rmdir(const char *),sys_truncate(const char *,long);
extern long sys_readlinkat(int,const char *,char *,int),sys_symlinkat(const char *,int,const char *);
extern long sys_lstat(const char *,void *),sys_lstat64(const char *,void *),sys_stat(const char *,void *),sys_stat64(const char *,void *);
extern long sys_fstatat(int,const char *,void *,int),sys_fstatat64(int,const char *,void *,int),sys_fstat(int,void *),sys_fstat64(int,void *);
static int conversions;
int dearust_get_perthread_wd(void){return AT_FDCWD;}
int atfd(int fd){return fd==-2?AT_FDCWD:fd;}
int atflags_bsd_to_linux(int flags){return flags==0?0:flags==0x20?AT_SYMLINK_NOFOLLOW:1;}
int vchroot_expand(struct expanded *args){assert(memchr(args->path,0,4096));return 0;}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){
    if(nr==310 || nr==311)assert(a==getpid());
    long result=syscall(nr,a,b,c,d,e,f);return result<0?-errno:result;
}
void __getuidgid(int32_t *uid,int32_t *gid){*uid=0x5b5b5b5b;*gid=0x5b5b5b5b;conversions++;}
static void paths(const char *bad){
    unsigned char output[144];char buf[16];
#define BAD(call) assert((call)==-14)
    BAD(sys_faccessat(-2,bad,0,0));BAD(sys_fchmodat(-2,bad,0600,0));
    BAD(do_linux_fchmodat(AT_FDCWD,bad,0600,0));BAD(sys_access(bad,0));BAD(sys_chmod(bad,0600));
    BAD(sys_linkat(-2,bad,-2,"/tmp/unused",0));BAD(sys_linkat(-2,"/",-2,bad,0));
    BAD(sys_renameat(-2,bad,-2,"/tmp/unused"));BAD(sys_renameat(-2,"/",-2,bad));
    BAD(sys_unlinkat(-2,bad,0));BAD(sys_mkdirat(-2,bad,0600));BAD(sys_rmdir(bad));BAD(sys_truncate(bad,0));
    BAD(sys_readlinkat(-2,bad,buf,sizeof(buf)));BAD(sys_symlinkat("unused",-2,bad));
    BAD(sys_lstat(bad,output));BAD(sys_lstat64(bad,output));BAD(sys_stat(bad,output));BAD(sys_stat64(bad,output));
    BAD(sys_fstatat(-2,bad,output,0));BAD(sys_fstatat64(-2,bad,output,0));
#undef BAD
}
static long metadata(unsigned which,int fd,const char *path,void *output){
    switch(which){case 0:return sys_fstat(fd,output);case 1:return sys_fstat64(fd,output);
    case 2:return sys_lstat(path,output);case 3:return sys_lstat64(path,output);
    case 4:return sys_stat(path,output);case 5:return sys_stat64(path,output);
    case 6:return sys_fstatat(-2,path,output,0);default:return sys_fstatat64(-2,path,output,0);}
}
int main(void){
    long page=sysconf(_SC_PAGESIZE);assert(page==4096);
    char *mapping=mmap(NULL,3*page,PROT_READ|PROT_WRITE,MAP_ANONYMOUS|MAP_PRIVATE,-1,0);assert(mapping!=MAP_FAILED);
    assert(mprotect(mapping+page,page,PROT_NONE)==0);
    char *unmapped=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_ANONYMOUS|MAP_PRIVATE,-1,0);assert(unmapped!=MAP_FAILED && munmap(unmapped,page)==0);
    paths(unmapped);paths((void *)(uintptr_t)1);paths(mapping+page);
    mapping[page-1]='x';paths(mapping+page-1);
    char file[]="/tmp/dearust-pointer-XXXXXX";int fd=mkstemp(file);assert(fd>=0 && write(fd,"payload",7)==7);
    size_t len=strlen(file)+1;char *edge=mapping+page-len;memcpy(edge,file,len);
    for(unsigned which=0;which<8;which++){
        unsigned length=(which&1)?144:120;unsigned char out[176];memset(out,0xa5,sizeof(out));
        assert(metadata(which,fd,edge,out+16)==0);
        for(unsigned i=0;i<16;i++)assert(out[i]==0xa5);
        unsigned char expected[144];memcpy(expected,out+16,length);
        struct stat decoded=native_view(expected,which&1);
        assert(decoded.st_size==7 && decoded.st_uid==0x5b5b5b5b && decoded.st_gid==0x5b5b5b5b);
        for(unsigned i=16+length;i<sizeof(out);i++)assert(out[i]==0xa5);
        assert(metadata(which,fd,file,(void *)(uintptr_t)1)==-14);
        assert(metadata(which,fd,file,mapping+page)==-14);
        char *hole=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_ANONYMOUS|MAP_PRIVATE,-1,0);
        assert(hole!=MAP_FAILED && munmap(hole,page)==0);
        assert(metadata(which,fd,file,hole)==-14);
        memset(mapping+page-64,0xa5,64);assert(metadata(which,fd,file,mapping+page-64)==-14);
        assert(memcmp(mapping+page-64,expected,64)==0);
        assert(mprotect(mapping+2*page,page,PROT_READ)==0);
        assert(metadata(which,fd,file,mapping+2*page)==-14);
        assert(mprotect(mapping+2*page,page,PROT_READ|PROT_WRITE)==0);
        memcpy(edge,file,len);
    }
    assert(sys_fstat(-1,(void *)(uintptr_t)1)==-9 && sys_fstat64(-1,(void *)(uintptr_t)1)==-9);
    pid_t child=fork();assert(child>=0);if(child==0){unsigned char out[144];assert(sys_fstat64(fd,out)==0);_exit(0);}
    int status;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
    assert(conversions==48 && close(fd)==0 && unlink(file)==0 && munmap(mapping,3*page)==0);
    puts("Pointer boundaries: invalid/unmapped/protected addresses, readable path prefixes, exact metadata sizes, partial writes and fork");
    return 0;
}
