/* Darling raw syscall entries and libc identity consistency in owned children. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
int main(void) {
    pid_t parent=getpid(),group=getpgrp(),session=getsid(0);
    assert(parent>0 && group>0 && session>0);
    for(int new_session=0;new_session<2;new_session++) {
        pid_t child=fork();assert(child>=0);
        if(child==0) {
            pid_t self=getpid();assert(self!=parent && getppid()==parent);
            errno=0;assert(syscall(SYS_setpgid,0,-1)==-1 && errno==EINVAL);
            if(new_session) {
                assert(syscall(SYS_setsid)==self);
                assert(getsid(0)==self && getsid(self)==self);
                assert(getpgrp()==self && getpgid(0)==self && getpgid(self)==self);
                errno=0;assert(syscall(SYS_setsid)==-1 && errno==EPERM);
                errno=0;assert(syscall(SYS_setpgid,0,0)==-1 && errno==EPERM);
                errno=0;assert(setpgid(self,self)==-1 && errno==EPERM);
            } else {
                assert(syscall(SYS_setpgid,0,0)==0);
                assert(getpgrp()==self && getpgid(0)==self && getpgid(self)==self);
                assert(getsid(0)==session && getppid()==parent);
                assert(setpgid(self,self)==0);
                errno=0;assert(syscall(SYS_setsid)==-1 && errno==EPERM);
            }
            assert(getpid()==self && getppid()==parent);
            _exit(0);
        }
        int status;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
    }
    assert(getpid()==parent && getpgrp()==group && getpgid(0)==group && getsid(0)==session);
    puts("Rust process control regression checks passed");
}
