/* Forking from an application signal handler creates a fresh server thread in
 * the child. Its return must not pop the parent's RPC interrupt frame. The
 * metadata concurrency/fault-copy case is in guarded-copy-regression.c. */
#include <assert.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <sys/wait.h>
#include <unistd.h>
#ifndef DEARUST_HANDLER_FORK_WAIT
#define DEARUST_HANDLER_FORK_WAIT() ((void)0)
#endif
static volatile sig_atomic_t entered,child_return,child_pid;
static pthread_t target;
static void handler(int signal){
    assert(signal==SIGUSR2);if(entered)return;entered=1;
    pid_t pid=fork();assert(pid>=0);
    if(pid==0)child_return=1;else child_pid=pid;
}
static void *sender(void *unused){
    (void)unused;usleep(10000);assert(pthread_kill(target,SIGUSR2)==0);return NULL;
}
int main(void){
    alarm(15);target=pthread_self();struct sigaction action={0};
    action.sa_handler=handler;sigemptyset(&action.sa_mask);
    assert(sigaction(SIGUSR2,&action,NULL)==0);pthread_t worker;
    assert(pthread_create(&worker,NULL,sender,NULL)==0);
    /* Keep this independent of blocked-RPC continuation semantics. */
    while(!entered || child_pid==0){if(child_return)_exit(0);DEARUST_HANDLER_FORK_WAIT();}
    assert(pthread_join(worker,NULL)==0);int status;
    assert(waitpid(child_pid,&status,0)==child_pid && WIFEXITED(status) && WEXITSTATUS(status)==0);
    puts("Rust signal handler fork regression checks passed");return 0;
}
