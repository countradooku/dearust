// Regression cases intentionally outside the valid-input C/Rust differential.
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <darling/emulation/common/guarded/table.h>
unsigned __int128 cerror(int error) { (void)error; abort(); }
_Noreturn void __simple_abort(void) { abort(); }
static int replacement;
static void replace_guard(int fd) {
    // Removing the old guard may report absent: Rust retires before callbacks.
    int removed = guard_table_remove(fd);
    assert(removed == 0 || removed == -2);
    assert(guard_table_add(fd, guard_flag_prevent_close, NULL) == 0);
    replacement++;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    if (strcmp(argv[1], "full") == 0) {
        for (int fd = 0; fd < 1024; ++fd) assert(guard_table_add(fd, 1, NULL) == 0);
        int result = guard_table_check(-3, 1);
        assert(result == 0);
        for (int fd = 0; fd < 1024; ++fd) assert(guard_table_remove(fd) == 0);
        for (int fd = -3; fd < 0; ++fd) {
            assert(guard_table_add(fd, 1, NULL) == -9);
            assert(guard_table_modify(fd, 1, NULL) == -9);
            assert(guard_table_remove(fd) == -9);
        }
        assert(guard_table_add(INT_MIN, 1, NULL) == -9);
        puts("negative fds rejected after full-table search");
    } else if (strcmp(argv[1], "sentinel") == 0) {
        int result = guard_table_modify(-1, 1, NULL);
        printf("modify(-1)=%d check(-1)=%d\n", result, guard_table_check(-1, 1));
        return result == -9 && guard_table_check(-1, 1) == 0 ? 0 : 1;
    } else if (strcmp(argv[1], "callback") == 0) {
        guard_entry_options_t options = {.close = replace_guard};
        assert(guard_table_add(5, guard_flag_close_on_fork, &options) == 0);
        guard_table_postfork_child();
        assert(replacement == 1);
        int present = guard_table_check(5, guard_flag_prevent_close);
        printf("callback replacement preserved=%d\n", present);
        return present ? 0 : 1;
    } else { abort(); }
    return 0;
}
