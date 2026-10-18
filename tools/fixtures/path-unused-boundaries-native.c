/* Native test adapters only: unexpected calls fail, never production fallbacks.
 * A consolidated object contains other operations whose callbacks a particular
 * fixture does not implement. Strong, operation-specific fixture functions
 * override these weak symbols. No raw syscall/expansion callback is supplied. */
#include <stdlib.h>
__attribute__((weak)) int atfd(int fd) { (void)fd; abort(); }
__attribute__((weak)) int atflags_bsd_to_linux(int flags) { (void)flags; abort(); }
__attribute__((weak)) int dearust_get_perthread_wd(void) { abort(); }
