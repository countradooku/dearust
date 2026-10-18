/* Limits change only in the owned child; parent limit is verified unchanged. */
#include <assert.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
static void check(void) {
    struct rlimit limit;assert(getrlimit(RLIMIT_NOFILE,&limit)==0);
    uint64_t value=limit.rlim_cur;
    long expected=value>INT_MAX?INT_MAX:(long)value;
    assert(syscall(SYS_getdtablesize)==expected && getdtablesize()==expected);
}
int main(void) {
    struct rlimit before,after;assert(getrlimit(RLIMIT_NOFILE,&before)==0);check();
    pid_t child=fork();assert(child>=0);
    if(child==0) {
        struct rlimit limit=before;
        if(limit.rlim_cur>64)limit.rlim_cur=64;
        assert(setrlimit(RLIMIT_NOFILE,&limit)==0);check();
        assert(setrlimit(RLIMIT_NOFILE,&before)==0);check();_exit(0);
    }
    int status;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
    assert(getrlimit(RLIMIT_NOFILE,&after)==0);
    assert(before.rlim_cur==after.rlim_cur && before.rlim_max==after.rlim_max);check();
    puts("Rust getdtablesize regression checks passed");return 0;
}
