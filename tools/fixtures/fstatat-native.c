/* Native boundary adapters, not a substitute for guest TLS/vchroot/layout tests. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "guest-memory-native.h"
#include "darwin-stat-native.h"
struct expanded { char path[4096]; unsigned flags; int dfd; };
struct output { _Alignas(8) unsigned char value[144]; int wide; unsigned char tail[16]; };
extern long sys_fstatat(int, const char *, void *, int);
extern long sys_fstatat64(int, const char *, void *, int);
static int expansion_error, expanded_calls, flag_calls, current_wd = AT_FDCWD;
static int expect_fd, expect_follow, snapshot_mode, final_queries;
int atfd(int fd) { return fd == -2 ? current_wd : fd; }
int atflags_bsd_to_linux(int flags) {
    flag_calls++;
    int result = 0;
    if (flags & 0x20) result |= AT_SYMLINK_NOFOLLOW;
    if (flags & (0x80 | 0x100)) result |= AT_REMOVEDIR;
    if (flags & 0x40) result |= AT_SYMLINK_FOLLOW;
    if (flags & 0x400) result |= AT_EMPTY_PATH;
    return flags & ~(0x20 | 0x80 | 0x100 | 0x40 | 0x10 | 0x200 | 0x400) ? 1 : result;
}
int vchroot_expand(struct expanded *args) {
    assert(args->dfd == expect_fd && args->flags == (unsigned)expect_follow);
    assert(memchr(args->path, 0, sizeof(args->path)));
    expanded_calls++;
    return expansion_error;
}
int vchroot_expand_stat(struct expanded *args, void *metadata) {
    int result = vchroot_expand(args);
    if(result < 0 || !snapshot_mode) return result < 0 ? result : 1;
    assert(fstatat(args->dfd,args->path,metadata,AT_SYMLINK_NOFOLLOW)==0);
    return 0;
}
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    long memory_result; if(dearust_native_memory_syscall(a,b,c,d,e,f,nr,&memory_result)) return memory_result;

    assert(nr == SYS_newfstatat && e == 0 && f == 0);
    final_queries++;
    long result = syscall(nr, a, b, c, d, e, f);
    return result < 0 ? -errno : result;
}
_Static_assert(sizeof(struct stat) == 144, "Linux x64 kernel stat layout");
void __getuidgid(int32_t *uid,int32_t *gid){*uid=(int32_t)getuid();*gid=(int32_t)getgid();}

int main(void) {
    char directory[] = "/tmp/dearust-fstatat-XXXXXX";
    assert(mkdtemp(directory));
    int dfd = open(directory, O_RDONLY | O_DIRECTORY); assert(dfd >= 0);
    int fd = openat(dfd, "file", O_CREAT | O_EXCL | O_WRONLY, 0600);
    assert(fd >= 0 && write(fd, "payload", 7) == 7 && close(fd) == 0);
    assert(symlinkat("file", dfd, "link") == 0);
    struct output output, before;
    for (int wide = 0; wide < 2; wide++) {
        long (*call)(int, const char *, void *, int) = wide ? sys_fstatat64 : sys_fstatat;
        memset(&output, 0xa5, sizeof(output)); before = output;
        expanded_calls = flag_calls = 0;
        assert(call(dfd, NULL, &output, 0) == -14);
        assert(call(dfd, (const char *)(uintptr_t)1, NULL, 0) == -14);
        char path[4096]; memset(path, 'x', sizeof(path));
        assert(call(dfd, path, &output, 0) == -63);
        assert(expanded_calls == 0 && flag_calls == 0);
        expect_fd = dfd; expect_follow = 1; expansion_error = -36;
        assert(call(dfd, "file", &output, 0x8000) == -63);
        assert(flag_calls == 0); expansion_error = 0;
        assert(call(dfd, "file", &output, 0x8000) == -22);
        assert(memcmp(&before, &output, sizeof(output)) == 0);
        assert(call(dfd, "link", &output, 0) == 0);
        assert(S_ISREG(native_view(output.value,wide).st_mode) && native_view(output.value,wide).st_size == 7);
        for (size_t i = 0; i < sizeof(output.tail); i++) assert(output.tail[i] == 0xa5);
        expect_follow = 0;
        assert(call(dfd, "link", &output, 0x20) == 0);
        assert(S_ISLNK(native_view(output.value,wide).st_mode) && native_view(output.value,wide).st_size == 4);
        before = output; expect_follow = 1;
        assert(call(dfd, "missing", &output, 0) == -2);
        assert(memcmp(&before, &output, sizeof(output)) == 0);
        current_wd = dfd;
        assert(call(-2, "file", &output, 0) == 0 && S_ISREG(native_view(output.value,wide).st_mode));
        /* Snapshot0 avoids a duplicate query only for exactly supported Linux
         * flags. Validation stays after expansion, including unsupported flags. */
        snapshot_mode=1;
        int queries=final_queries;
        assert(call(dfd,"file",&output,0)==0 && final_queries==queries);
        expect_follow=0;
        assert(call(dfd,"file",&output,0x20)==0 && final_queries==queries);
        expect_follow=1; before=output;
        assert(call(dfd,"file",&output,0x8000)==-22 && final_queries==queries);
        assert(memcmp(&before,&output,sizeof(output))==0);
        assert(call(dfd,"file",&output,0x40)==-22 && final_queries==queries+1);
        assert(memcmp(&before,&output,sizeof(output))==0);
        snapshot_mode=0;
        expect_fd = -42;
        assert(call(-42, "file", &output, 0) == -9);
        expect_fd = AT_FDCWD; current_wd = AT_FDCWD;
        assert(call(-2, directory, &output, 0) == 0 && S_ISDIR(native_view(output.value,wide).st_mode));
    }
    assert(unlinkat(dfd, "link", 0) == 0 && unlinkat(dfd, "file", 0) == 0);
    assert(close(dfd) == 0 && rmdir(directory) == 0);
    puts("fstatat/fstatat64: bounded/null paths, expansion/flag ordering, dirfd, cwd, symlink policy, errno and output preservation");
}
