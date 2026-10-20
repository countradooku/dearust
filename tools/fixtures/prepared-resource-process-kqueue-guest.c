// Ordinary guest EVFILT_PROC lifecycle checks. Child pipes establish registration
// before fork/exit; no server RPC transport or sender-validation probes are used.
#include <sys/types.h>
#include <sys/event.h>
#include <sys/wait.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <mach/mach.h>

static void require(int condition, const char* text) {
    if (!condition) { fprintf(stderr, "%s (errno=%d)\n", text, errno); exit(1); }
}
static void byte_read(int fd) {
    char byte;
    ssize_t count;
    do { count = read(fd, &byte, 1); } while (count < 0 && errno == EINTR);
    require(count == 1, "child synchronization read");
}
static void byte_write(int fd) { require(write(fd, "x", 1) == 1, "child synchronization write"); }
static void checkin_child(void) {
    mach_port_t port;
    require(mach_port_allocate(mach_task_self(), MACH_PORT_RIGHT_RECEIVE, &port) == KERN_SUCCESS,
            "child Mach port allocation/checkin");
    require(mach_port_destroy(mach_task_self(), port) == KERN_SUCCESS, "child Mach port retirement");
}
static void add(int queue, pid_t child, unsigned flags) {
    struct kevent event;
    EV_SET(&event, child, EVFILT_PROC, EV_ADD | EV_ENABLE | EV_CLEAR, flags, 0, NULL);
    const int result = kevent(queue, &event, 1, NULL, 0, NULL);
    if (result != 0) fprintf(stderr, "process filter registration: flags=%08x result=%d child=%d\n", flags, result, child);
    require(result == 0, "register process filter");
}
static struct kevent next(int queue, pid_t child, unsigned expected) {
    struct kevent event;
    const struct timespec timeout = {10, 0};
    int count;
    do { count = kevent(queue, NULL, 0, &event, 1, &timeout); } while (count < 0 && errno == EINTR);
    require(count == 1, "wait for process event");
    require(event.filter == EVFILT_PROC && event.ident == (uintptr_t)child &&
            !(event.flags & EV_ERROR), "process event identity/filter");
    require(event.fflags == expected, "process event flags/order");
    if (expected == NOTE_EXIT) require(event.flags & EV_EOF, "exit event EOF");
    return event;
}
static void waited(pid_t child) {
    int status;
    require(waitpid(child, &status, 0) == child && WIFEXITED(status) &&
            WEXITSTATUS(status) == 0, "wait child exit");
}
static void exit_case(int extra_filter) {
    int ready[2], checked[2]; require(pipe(ready) == 0 && pipe(checked) == 0, "create exit synchronization pipe");
    pid_t child = fork(); require(child >= 0, "fork exit child");
    if (!child) { close(checked[0]); checkin_child(); byte_write(checked[1]); close(checked[1]); close(ready[1]); byte_read(ready[0]); close(ready[0]); _exit(0); }
    close(checked[1]); byte_read(checked[0]); close(checked[0]);
    close(ready[0]); int queue = kqueue(); require(queue >= 0, "create exit kqueue");
    add(queue, child, NOTE_EXIT | (extra_filter ? NOTE_EXEC : 0));
    byte_write(ready[1]); close(ready[1]);
    (void)next(queue, child, NOTE_EXIT); waited(child); close(queue);
}
static void fork_then_exit(void) {
    int begin[2], finish[2], checked[2];
    require(pipe(begin) == 0 && pipe(finish) == 0 && pipe(checked) == 0, "create fork synchronization pipes");
    pid_t child = fork(); require(child >= 0, "fork observed parent");
    if (!child) {
        close(checked[0]); checkin_child(); byte_write(checked[1]); close(checked[1]);
        close(begin[1]); close(finish[1]); byte_read(begin[0]); close(begin[0]);
        pid_t grandchild = fork();
        if (grandchild < 0) _exit(2);
        if (!grandchild) _exit(0);
        waited(grandchild);
        byte_read(finish[0]); close(finish[0]); _exit(0);
    }
    close(checked[1]); byte_read(checked[0]); close(checked[0]);
    close(begin[0]); close(finish[0]);
    int queue = kqueue(); require(queue >= 0, "create fork kqueue");
    add(queue, child, NOTE_FORK | NOTE_EXIT);
    byte_write(begin[1]); close(begin[1]);
    (void)next(queue, child, NOTE_FORK);
    byte_write(finish[1]); close(finish[1]);
    (void)next(queue, child, NOTE_EXIT); waited(child); close(queue);
}
int main(void) {
    exit_case(0);
    exit_case(1);
    fork_then_exit();
    puts("process-kqueue exit/filter/fork-order ok");
    return 0;
}
