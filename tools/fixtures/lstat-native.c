// Native boundary adapter test; guest TLS, vchroot and Darwin field conversion
// are validated separately by Mach-O app probes, not simulated here.
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "guest-memory-native.h"
struct expanded { char path[4096]; unsigned flags; int dfd; };
struct output { struct stat value; int wide; };
extern long sys_lstat(const char *, void *);
extern long sys_lstat64(const char *, void *);
static int expanded_calls;
static int expansion_error;
int dearust_get_perthread_wd(void) { return -100; }
int vchroot_expand(struct expanded *args) {
    assert(args->flags == 0 && args->dfd == -100);
    assert(memchr(args->path, 0, sizeof(args->path)));
    expanded_calls++;
    return expansion_error;
}
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    long memory_result; if(dearust_native_memory_syscall(a,b,c,d,e,f,nr,&memory_result)) return memory_result;

    assert(nr == SYS_lstat);
    long result = syscall(nr, a, b, c, d, e, f);
    return result < 0 ? -errno : result;
}
_Static_assert(sizeof(struct stat) == 144, "Linux x64 kernel stat layout size");
static int last_wide;
void stat_linux_to_bsd(const void *input, void *output) {
    memcpy(output,input,120); last_wide=0;
}
void stat_linux_to_bsd64(const void *input, void *output) {
    memcpy(output,input,144); last_wide=1;
}

int main(void) {
    struct output output;
    for (int wide = 0; wide < 2; wide++) {
        long (*call)(const char *, void *) = wide ? sys_lstat64 : sys_lstat;
        expanded_calls = 0;
        assert(call(NULL, &output) == -14);
        assert(call("/", NULL) == -14);
        char too_long[4096]; memset(too_long, 'x', sizeof(too_long));
        assert(call(too_long, &output) == -63);
        assert(expanded_calls == 0);
        expansion_error = -36;
        assert(call("/", &output) == -63);
        expansion_error = 0;
        assert(call("", &output) == -2);
        char directory[] = "/tmp/dearust-lstat-XXXXXX";
        assert(mkdtemp(directory));
        char target[128], link[128], missing[128];
        snprintf(target, sizeof(target), "%s/file", directory);
        snprintf(link, sizeof(link), "%s/link", directory);
        snprintf(missing, sizeof(missing), "%s/missing", directory);
        FILE *file = fopen(target, "w"); assert(file);
        assert(fputs("lstat payload", file) >= 0); assert(fclose(file) == 0);
        assert(symlink("file", link) == 0);
        memset(&output, 0xa5, sizeof(output));
        assert(call(target, &output) == 0);
        assert(last_wide == wide && S_ISREG(output.value.st_mode));
        assert(output.value.st_size == 13);
        assert(call(link, &output) == 0);
        assert(last_wide == wide && S_ISLNK(output.value.st_mode));
        assert(output.value.st_size == 4);
        struct output before = output;
        assert(call(missing, &output) == -2);
        assert(memcmp(&before, &output, sizeof(output)) == 0);
        assert(unlink(link) == 0 && unlink(target) == 0 && rmdir(directory) == 0);
    }
    puts("lstat/lstat64: null and bounded paths, vchroot errors, raw syscall regular/symlink metadata, unchanged output on errors");
}
