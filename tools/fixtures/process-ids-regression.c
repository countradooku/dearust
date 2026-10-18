/* Actual Darling namespace-local identifiers and isolated child-session changes. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <sys/types.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
int main(void) {
    pid_t parent=getpid(),group=getpgrp(),session=getsid(0);
    assert(parent>0 && getpid()==parent && getppid()>0 && group>0 && session>0);
    assert(syscall(SYS_getpid)==parent && syscall(SYS_getppid)>0);
    assert(getpgid(0)==group && getpgid(parent)==group && getsid(parent)==session);
    errno=0;assert(getpgid(-1)==-1 && errno==ESRCH);
    errno=0;assert(getsid(-1)==-1 && errno==ESRCH);
    pid_t child=fork();assert(child>=0);
    if(child==0) {
        pid_t self=getpid();
        assert(self>0 && self!=parent && getppid()==parent);
        assert(syscall(SYS_getpid)==self && syscall(SYS_getppid)==parent);
        assert(getpgrp()==group && getpgid(0)==group && getpgid(self)==group);
        assert(getsid(0)==session && getsid(self)==session);
        /* A newly forked child is not its inherited group's leader. */
        assert(setsid()==self);
        assert(getpid()==self && getppid()==parent);
        assert(syscall(SYS_getpid)==self && syscall(SYS_getppid)==parent);
        assert(getpgrp()==self && getpgid(0)==self && getpgid(self)==self);
        assert(getsid(0)==self && getsid(self)==self);
        _exit(0);
    }
    int status;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
    assert(getpid()==parent && getpgrp()==group && getsid(0)==session);
    errno=0;assert(getpgid(child)==-1 && errno==ESRCH);
    errno=0;assert(getsid(child)==-1 && errno==ESRCH);
    puts("Rust process identifiers regression checks passed");
}
