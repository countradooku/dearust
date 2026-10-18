/* Owned child-only Linux RLIMIT_NOFILE pressure, through actual Darwin entries. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <mach/mach.h>
#include <pthread.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
_Static_assert(sizeof(struct statfs)==2168,"Darwin statfs2168");
/* Linux syscall numbers/registers, independent of Darwin syscall() dispatch. */
static long linux_raw(long number,long a,long b,long c,long d){
    register long r10 __asm__("r10")=d;
    register long r8 __asm__("r8")=0;
    register long r9 __asm__("r9")=0;
    long result;
    __asm__ volatile("syscall":"=a"(result):"a"(number),"D"(a),"S"(b),"d"(c),"r"(r10),"r"(r8),"r"(r9):"rcx","r11","cc","memory");
    return result;
}
struct linux_limit{uint64_t current,maximum;};
static void limit(uint64_t maximum){struct linux_limit value={maximum,maximum};assert(linux_raw(302,0,7,(long)&value,0)==0);}
static unsigned entries(int fd){
    assert(linux_raw(8,fd,0,0,0)==0);unsigned count=0;unsigned char buffer[4096];
    for(;;){
        long length=linux_raw(217,fd,(long)buffer,sizeof(buffer),0);assert(length>=0);if(length==0)break;
        size_t offset=0;
        while(offset<(size_t)length){
            assert((size_t)length-offset>=20);uint16_t size;memcpy(&size,buffer+offset+16,2);
            assert(size>=20 && size<=(size_t)length-offset);
            const char *name=(const char*)buffer+offset+19;assert(memchr(name,0,size-19));
            if(strcmp(name,".") && strcmp(name,".."))count++;
            offset+=size;
        }
    }
    return count;
}
struct output{uint64_t before;struct statfs fs;uint64_t after;};
static void valid(const struct output *output,const struct statfs *reference){
    const struct statfs *fs=&output->fs;
    assert(output->before==UINT64_C(0xa5a5a5a5a5a5a5a5) && output->after==UINT64_C(0xa5a5a5a5a5a5a5a5));
    assert(fs->f_bsize==reference->f_bsize && fs->f_iosize==reference->f_iosize && fs->f_blocks==reference->f_blocks);
    assert(memcmp(&fs->f_fsid,&reference->f_fsid,sizeof(fs->f_fsid))==0);
    assert(fs->f_type==reference->f_type && fs->f_flags==reference->f_flags);
    assert(fs->f_fstypename[0] && strcmp(fs->f_fstypename,reference->f_fstypename)==0);
    assert(fs->f_mntonname[0]=='/' && strcmp(fs->f_mntonname,reference->f_mntonname)==0);
    assert(fs->f_mntfromname[0] && strcmp(fs->f_mntfromname,reference->f_mntfromname)==0);
    assert(fs->f_owner==0 && fs->f_fssubtype==0 && fs->f_flags_ext==0);
    for(unsigned i=0;i<7;i++)assert(fs->f_reserved[i]==0);
}
static volatile sig_atomic_t signals;
static void user_signal(int signal){assert(signal==SIGUSR1);signals++;}
static void child(const char *path,int original,unsigned mode){
    int file=original;if(mode==2){assert(dup2(original,0)==0);file=0;}
    /* Complete per-child runtime/server setup before denying descriptor creation. */
    task_basic_info_data_t info;mach_msg_type_number_t count=TASK_BASIC_INFO_COUNT;
    assert(task_info(mach_task_self(),TASK_BASIC_INFO,(task_info_t)&info,&count)==KERN_SUCCESS);
    (void)getuid();
    struct output output;memset(&output,0xa5,sizeof(output));
    assert(syscall(SYS_fstatfs64,file,&output.fs)==0);struct statfs reference=output.fs;
    int task_directory=(int)linux_raw(257,-100,(long)"/proc/self/task",0x90000,0);
    int fd_directory=(int)linux_raw(257,-100,(long)"/proc/self/fd",0x90000,0);
    assert(task_directory>=0 && fd_directory>=0);unsigned initial_threads=entries(task_directory);
    size_t page=(size_t)sysconf(_SC_PAGESIZE);unsigned char *memory=mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);assert(memory!=MAP_FAILED);
    memset(memory,0xa5,page*2);assert(mprotect(memory+page,page,PROT_NONE)==0);
    struct sigaction handler={0},saved;handler.sa_handler=user_signal;assert(sigemptyset(&handler.sa_mask)==0 && sigaction(SIGUSR1,&handler,&saved)==0);
    sigset_t blocked,previous,current;assert(sigemptyset(&blocked)==0 && sigaddset(&blocked,SIGBUS)==0 && sigaddset(&blocked,SIGSEGV)==0);
    assert(pthread_sigmask(SIG_BLOCK,&blocked,&previous)==0);
    if(mode==0){
        limit(64);
        unsigned duplicate_count=0;
        for(;;){long result=linux_raw(72,file,1030,0,0);if(result==-24)break;assert(result>=0);duplicate_count++;assert(duplicate_count<64);}
        assert(duplicate_count>0);
    }else limit(0);
    unsigned initial_fds=entries(fd_directory);
    for(unsigned repeat=0;repeat<10;repeat++){
        int path_numbers[]={SYS_statfs,SYS_statfs64},fd_numbers[]={SYS_fstatfs,SYS_fstatfs64};
        for(unsigned wide=0;wide<2;wide++){
            memset(&output,0xa5,sizeof(output));assert(syscall(path_numbers[wide],path,&output.fs)==0);valid(&output,&reference);
            memset(&output,0xa5,sizeof(output));assert(syscall(fd_numbers[wide],file,&output.fs)==0);valid(&output,&reference);
        }
    }
    errno=0;assert(syscall(SYS_fstatfs64,-42,NULL)==-1 && errno==EBADF);
    char missing[4096];int length=snprintf(missing,sizeof(missing),"%s.missing",path);assert(length>0 && (size_t)length<sizeof(missing));
    errno=0;assert(syscall(SYS_statfs64,missing,NULL)==-1 && errno==ENOENT);
    void *bad[]={NULL,memory+page,memory+page-32};
    for(unsigned index=0;index<3;index++){
        errno=0;assert(syscall(SYS_fstatfs64,file,bad[index])==-1 && errno==EFAULT);
        errno=0;assert(syscall(SYS_statfs64,path,bad[index])==-1 && errno==EFAULT);
    }
    assert(memory[page-33]==0xa5 && mprotect(memory,page,PROT_READ)==0);
    errno=0;assert(syscall(SYS_fstatfs64,file,memory)==-1 && errno==EFAULT);
    errno=0;assert(syscall(SYS_statfs64,path,memory)==-1 && errno==EFAULT);
    assert(entries(fd_directory)==initial_fds && entries(task_directory)==initial_threads && signals==0);
    struct sigaction after;assert(sigaction(SIGUSR1,NULL,&after)==0 && after.sa_handler==user_signal);
    assert(pthread_sigmask(SIG_BLOCK,NULL,&current)==0 && sigismember(&current,SIGBUS)==1 && sigismember(&current,SIGSEGV)==1);
    assert(pthread_sigmask(SIG_SETMASK,&previous,NULL)==0 && sigaction(SIGUSR1,&saved,NULL)==0);
    _exit(0);
}
int main(void){
    alarm(120);char path[]="/private/tmp/dearust-filesystem-pressure-XXXXXX";int fd=mkstemp(path);assert(fd>=0);
    struct linux_limit before,after;assert(linux_raw(302,0,7,0,(long)&before)==0);
    for(unsigned mode=0;mode<3;mode++){
        pid_t pid=fork();assert(pid>=0);if(pid==0)child(path,fd,mode);
        int status;assert(waitpid(pid,&status,0)==pid && WIFEXITED(status) && WEXITSTATUS(status)==0);
    }
    assert(linux_raw(302,0,7,0,(long)&after)==0 && memcmp(&before,&after,sizeof(before))==0);
    assert(close(fd)==0 && unlink(path)==0);
    puts("Dearust filesystem descriptor pressure and zero-limit regression checks passed");return 0;
}
