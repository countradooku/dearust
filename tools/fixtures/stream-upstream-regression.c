/* GPL-3.0-or-later. Exact original-vs-corrected stream bug reproducer.
 * Compile with EXPECT_ORIGINAL=1 for the pinned read.c object, zero for fd.o.
 * Caller declaration intentionally matches the real 64-bit syscall entry ABI;
 * the old C definition incorrectly narrows its third argument to int. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
#ifndef EXPECT_ORIGINAL
#error Specify EXPECT_ORIGINAL explicitly
#endif
extern long sys_read_nocancel(int,void *,size_t);
long sys_pthread_canceled(int action) { assert(action==0); return 1; }
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr) {
    long result=syscall(nr,a,b,c,d,e,f); return result==-1 ? -errno : result;
}
long __real_ioctl(int fd,int request,void *arg) {
    long result=syscall(SYS_ioctl,fd,request,arg); return result==-1 ? -errno : result;
}
struct xnu_rlimit { uint64_t current, maximum; long reserved[20]; };
long sys_getrlimit(unsigned which,struct xnu_rlimit *value) {
    struct rlimit real; assert(which==8 && getrlimit(RLIMIT_NOFILE,&real)==0);
    value->current=real.rlim_cur-1; value->maximum=real.rlim_max-1; return 0;
}
static void terminal(void) {
    int master=posix_openpt(O_RDWR|O_NOCTTY); assert(master>=0);
    assert(grantpt(master)==0 && unlockpt(master)==0);
    char name[128]; assert(ptsname_r(master,name,sizeof(name))==0);
    int slave=open(name,O_RDWR|O_NOCTTY); assert(slave>=0);
    pid_t owner=fork(); assert(owner>=0);
    if(owner==0) {
        assert(setsid()>=0 && ioctl(slave,TIOCSCTTY,0)==0);
        pid_t background=fork(); assert(background>=0);
        if(background==0) {
            assert(setpgid(0,0)==0 && signal(SIGTTIN,SIG_IGN)!=SIG_ERR);
            char byte; errno=0;
            assert(read(slave,&byte,1)==-1 && errno==EIO);
            assert(sys_read_nocancel(slave,&byte,1)==(EXPECT_ORIGINAL ? 0 : -5));
            _exit(0);
        }
        int status; assert(waitpid(background,&status,0)==background);
        assert(WIFEXITED(status) && WEXITSTATUS(status)==0); _exit(0);
    }
    int status; assert(waitpid(owner,&status,0)==owner);
    assert(WIFEXITED(status) && WEXITSTATUS(status)==0);
    assert(close(slave)==0 && close(master)==0);
}
int main(void) {
    char name[]="/tmp/dearust-original-stream-XXXXXX"; int fd=mkstemp(name);
    assert(fd>=0 && unlink(name)==0 && write(fd,"x",1)==1 && lseek(fd,0,SEEK_SET)==0);
    char byte=0;
    assert(sys_read_nocancel(fd,&byte,UINT64_C(1)<<32)==(EXPECT_ORIGINAL ? 0 : -22));
    assert(lseek(fd,0,SEEK_CUR)==0 && byte==0 && close(fd)==0);
    terminal();
    printf("original=%d count_2pow32=%d background_slave_EIO=%d\n",EXPECT_ORIGINAL,
           EXPECT_ORIGINAL ? 0 : -22,EXPECT_ORIGINAL ? 0 : -5);
}
