/* Exercise the production fork bridge while other threads create RPC guards. */
#include <assert.h>
#include <mach/mach.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <sys/wait.h>
#include <unistd.h>
static atomic_int stop, cycles;
static void *rpc(void *unused) {
    (void)unused;
    mach_port_t port;
    assert(mach_port_allocate(mach_task_self(),MACH_PORT_RIGHT_RECEIVE,&port)==KERN_SUCCESS);
    assert(mach_port_destroy(mach_task_self(),port)==KERN_SUCCESS);
    return NULL;
}
static void *churn(void *unused) {
    (void)unused;
    while(!atomic_load(&stop)) {
        pthread_t child;
        assert(pthread_create(&child,NULL,rpc,NULL)==0);
        assert(pthread_join(child,NULL)==0);
        atomic_fetch_add(&cycles,1);
    }
    return NULL;
}
int main(void) {
    alarm(30);
    pthread_t worker;
    assert(pthread_create(&worker,NULL,churn,NULL)==0);
    while(!atomic_load(&cycles))usleep(1000);
    for(int i=0;i<20;++i) {
        pid_t child=fork();assert(child>=0);
        if(child==0) { alarm(5);rpc(NULL);_exit(0); }
        int status;assert(waitpid(child,&status,0)==child);
        assert(WIFEXITED(status)&&WEXITSTATUS(status)==0);
    }
    atomic_store(&stop,1);assert(pthread_join(worker,NULL)==0);
    assert(atomic_load(&cycles)>0);
    puts("Dearust concurrent fork and RPC thread churn passed");
    return 0;
}
