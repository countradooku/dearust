// Unavoidable guest TLS boundary; compile with the original emulation headers.
#include <darling/emulation/common/bsdthread/per_thread_wd.h>
int dearust_get_perthread_wd(void) { return get_perthread_wd(); }
