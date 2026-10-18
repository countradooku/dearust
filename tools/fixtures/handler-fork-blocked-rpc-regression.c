/* The child must cancel the parent's pending sleep RPC, not wait for a reply
 * on its refreshed socket. Ordinary parent signal interruption remains intact. */
#define DEARUST_HANDLER_FORK_WAIT() usleep(100)
#include "handler-fork-regression.c"
