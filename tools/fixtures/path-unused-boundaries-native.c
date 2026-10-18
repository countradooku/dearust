/* Native test adapters only: unexpected calls fail, never production fallbacks.
 * A consolidated object contains other operations whose callbacks a particular
 * fixture does not implement. Strong, operation-specific fixture functions
 * override these weak symbols. No raw syscall callback is supplied. */
#include <stdlib.h>
__attribute__((weak)) int atfd(int fd) { (void)fd; abort(); }
__attribute__((weak)) int atflags_bsd_to_linux(int flags) { (void)flags; abort(); }
__attribute__((weak)) int dearust_get_perthread_wd(void) { abort(); }

__attribute__((weak)) void stat_linux_to_bsd(const void *input, void *output) { (void)input; (void)output; abort(); }
__attribute__((weak)) void stat_linux_to_bsd64(const void *input, void *output) { (void)input; (void)output; abort(); }

__attribute__((weak)) int vchroot_expand(void *args) { (void)args; abort(); }
