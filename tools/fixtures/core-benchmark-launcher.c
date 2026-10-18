// SPDX-License-Identifier: GPL-3.0-or-later
#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static long long nanos(struct timespec value) {
    return (long long)value.tv_sec * 1000000000 + value.tv_nsec;
}

int main(int argc, char **argv) {
    if (argc != 4)
        return 2;
    struct timespec before, after;
    struct rusage usage;
    clock_gettime(CLOCK_MONOTONIC, &before);
    pid_t child = fork();
    if (child < 0)
        return 3;
    if (child == 0) {
        execl(argv[1], argv[1], argv[2], argv[3], (char *)0);
        _exit(127);
    }
    int status;
    while (wait4(child, &status, 0, &usage) < 0) {
        if (errno != EINTR)
            return 4;
    }
    clock_gettime(CLOCK_MONOTONIC, &after);
    if (!WIFEXITED(status) || WEXITSTATUS(status))
        return 5;
    double user = (double)usage.ru_utime.tv_sec * 1e6 + usage.ru_utime.tv_usec;
    double system = (double)usage.ru_stime.tv_sec * 1e6 + usage.ru_stime.tv_usec;
    printf("{\"wall_us\":%.3f,\"cpu_us\":%.3f,\"user_us\":%.3f,"
           "\"system_us\":%.3f,\"maxrss_kib\":%ld,\"minor_faults\":%ld,"
           "\"major_faults\":%ld,\"voluntary_switches\":%ld,"
           "\"involuntary_switches\":%ld}\n",
           (nanos(after) - nanos(before)) / 1000., user + system, user, system,
           usage.ru_maxrss, usage.ru_minflt, usage.ru_majflt,
           usage.ru_nvcsw, usage.ru_nivcsw);
    return 0;
}
