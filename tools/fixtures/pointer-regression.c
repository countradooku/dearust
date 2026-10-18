/* All eight raw Darwin metadata exports use actual legacy120/wide144 layouts.
 * Kernel-mediated access must fail safely without process-wide signal handlers. */
#define _DARWIN_NO_64_BIT_INODE 1
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
_Static_assert(sizeof(struct stat)==120,"raw Darwin legacy stat120");
_Static_assert(sizeof(struct stat64)==144,"raw Darwin stat64 size144");
union metadata {struct stat narrow;struct stat64 wide;unsigned char bytes[144];};
static void paths(const char *bad){
    union metadata output;char buf[16];
#define BAD(call) do{errno=0;assert((call)==-1 && errno==EFAULT);}while(0)
    BAD(syscall(SYS_faccessat,AT_FDCWD,bad,0,0));BAD(syscall(SYS_fchmodat,AT_FDCWD,bad,0600,0));
    BAD(syscall(SYS_access,bad,0));BAD(syscall(SYS_chmod,bad,0600));
    BAD(syscall(SYS_linkat,AT_FDCWD,bad,AT_FDCWD,"/tmp/unused",0));
    BAD(syscall(SYS_linkat,AT_FDCWD,"/",AT_FDCWD,bad,0));
    BAD(syscall(SYS_renameat,AT_FDCWD,bad,AT_FDCWD,"/tmp/unused"));
    BAD(syscall(SYS_renameat,AT_FDCWD,"/",AT_FDCWD,bad));
    BAD(syscall(SYS_unlinkat,AT_FDCWD,bad,0));BAD(syscall(SYS_mkdirat,AT_FDCWD,bad,0600));
    BAD(syscall(SYS_rmdir,bad));BAD(syscall(SYS_truncate,bad,(off_t)0));
    BAD(syscall(SYS_readlinkat,AT_FDCWD,bad,buf,sizeof(buf)));BAD(syscall(SYS_symlinkat,"unused",AT_FDCWD,bad));
    BAD(syscall(SYS_lstat,bad,&output));BAD(syscall(SYS_lstat64,bad,&output));
    BAD(syscall(SYS_stat,bad,&output));BAD(syscall(SYS_stat64,bad,&output));
    BAD(syscall(SYS_fstatat,AT_FDCWD,bad,&output,0));BAD(syscall(SYS_fstatat64,AT_FDCWD,bad,&output,0));
#undef BAD
}
static long metadata(unsigned which,int fd,const char *path,void *output){
    switch(which){case 0:return syscall(SYS_fstat,fd,output);case 1:return syscall(SYS_fstat64,fd,output);
    case 2:return syscall(SYS_lstat,path,output);case 3:return syscall(SYS_lstat64,path,output);
    case 4:return syscall(SYS_stat,path,output);case 5:return syscall(SYS_stat64,path,output);
    case 6:return syscall(SYS_fstatat,AT_FDCWD,path,output,0);default:return syscall(SYS_fstatat64,AT_FDCWD,path,output,0);}
}
int main(void){
    alarm(60);long page=sysconf(_SC_PAGESIZE);assert(page==4096);
    char *mapping=mmap(NULL,3*page,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);assert(mapping!=MAP_FAILED);
    assert(mprotect(mapping+page,page,PROT_NONE)==0);
    char *unmapped=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);assert(unmapped!=MAP_FAILED && munmap(unmapped,page)==0);
    paths(unmapped);paths((void *)(uintptr_t)1);paths(mapping+page);
    mapping[page-1]='x';paths(mapping+page-1);
    char file[]="/private/tmp/dearust-pointer-XXXXXX";int fd=mkstemp(file);assert(fd>=0 && write(fd,"payload",7)==7);
    struct stat reference;struct stat64 reference64;assert(fstat(fd,&reference)==0 && fstat64(fd,&reference64)==0);
    size_t len=strlen(file)+1;char *edge=mapping+page-len;memcpy(edge,file,len);
    for(unsigned which=0;which<8;which++){
        unsigned length=(which&1)?144:120;
        struct {unsigned char head[16];union metadata value;unsigned char tail[16];} out;
        memset(&out,0xa5,sizeof(out));assert(metadata(which,fd,edge,&out.value)==0);
        if(which&1){assert(S_ISREG(out.value.wide.st_mode) && out.value.wide.st_size==7 && out.value.wide.st_ino==reference64.st_ino);}
        else{assert(S_ISREG(out.value.narrow.st_mode) && out.value.narrow.st_size==7 && out.value.narrow.st_ino==reference.st_ino);}
        for(unsigned i=0;i<16;i++)assert(out.head[i]==0xa5 && out.tail[i]==0xa5);
        for(unsigned i=length;i<144;i++)assert(out.value.bytes[i]==0xa5);
#define BAD_OUTPUT(pointer) do{errno=0;assert(metadata(which,fd,file,(pointer))==-1 && errno==EFAULT);}while(0)
        BAD_OUTPUT((void *)(uintptr_t)1);BAD_OUTPUT(mapping+page);
        char *hole=mmap(NULL,page,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);
        assert(hole!=MAP_FAILED && munmap(hole,page)==0);BAD_OUTPUT(hole);
        memset(mapping+page-64,0xa5,64);BAD_OUTPUT(mapping+page-64);
        assert(memcmp(mapping+page-64,out.value.bytes,64)==0);
        assert(mprotect(mapping+2*page,page,PROT_READ)==0);BAD_OUTPUT(mapping+2*page);
        assert(mprotect(mapping+2*page,page,PROT_READ|PROT_WRITE)==0);
#undef BAD_OUTPUT
        memcpy(edge,file,len);
    }
    errno=0;assert(syscall(SYS_fstat,-1,(void *)(uintptr_t)1)==-1 && errno==EBADF);
    errno=0;assert(syscall(SYS_fstat64,-1,(void *)(uintptr_t)1)==-1 && errno==EBADF);
    pid_t child=fork();assert(child>=0);if(child==0){union metadata out;assert(syscall(SYS_fstat64,fd,&out)==0 && out.wide.st_size==7);_exit(0);}
    int status;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
    assert(close(fd)==0 && unlink(file)==0 && munmap(mapping,3*page)==0);
    puts("Rust pointer boundary regression checks passed");return 0;
}
