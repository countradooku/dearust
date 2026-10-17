/* A retained old task port must not preserve lock sets across exec. */
#include <mach/mach.h>
#include <mach/lock_set.h>
#include <mach/sync_policy.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
#define CHECK(expr) do { if (!(expr)) { fprintf(stderr, "line %d: %s\n", __LINE__, #expr); exit(1); } } while (0)
int main(void) {
    int ready[2], go[2];
    CHECK(pipe(ready) == 0 && pipe(go) == 0);
    pid_t child = fork();
    CHECK(child >= 0);
    if (!child) {
        close(ready[0]); close(go[1]);
        CHECK(MACH_PORT_VALID(mach_task_self()));
        CHECK(write(ready[1], "r", 1) == 1);
        char byte;
        CHECK(read(go[0], &byte, 1) == 1);
        execl("/bin/sleep", "sleep", "10", (char *)NULL);
        _exit(2);
    }
    close(ready[1]); close(go[0]);
    char byte;
    CHECK(read(ready[0], &byte, 1) == 1);
    close(ready[0]);
    task_t retained_task;
    lock_set_t locks;
    CHECK(task_for_pid(mach_task_self(), child, &retained_task) == KERN_SUCCESS);
    CHECK(lock_set_create(retained_task, &locks, 1, SYNC_POLICY_FIFO) == KERN_SUCCESS);
    CHECK(write(go[1], "g", 1) == 1);
    close(go[1]);
    kern_return_t kr = KERN_SUCCESS;
    for (int i = 0; i < 5000 && kr != KERN_LOCK_SET_DESTROYED; ++i) {
        kr = lock_try(locks, 0);
        if (kr == KERN_SUCCESS) {
            kern_return_t released = lock_release(locks, 0);
            CHECK(released == KERN_SUCCESS || released == KERN_LOCK_SET_DESTROYED);
        }
        if (kr != KERN_LOCK_SET_DESTROYED) usleep(1000);
    }
    CHECK(kr == KERN_LOCK_SET_DESTROYED);
    /* Observe destruction while the replacement program is still alive. */
    int status;
    CHECK(waitpid(child, &status, WNOHANG) == 0);
    CHECK(kill(child, SIGKILL) == 0);
    CHECK(waitpid(child, &status, 0) == child);
    CHECK(mach_port_deallocate(mach_task_self(), locks) == KERN_SUCCESS);
    CHECK(mach_port_deallocate(mach_task_self(), retained_task) == KERN_SUCCESS);
    puts("lockset exec retirement with retained task port: PASS");
    return 0;
}
