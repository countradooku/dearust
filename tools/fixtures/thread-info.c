/* Actual Darling ABI regression for Rust thread and executable-path queries. */
#include <assert.h>
#include <errno.h>
#include <libproc.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/time.h>
#include <unistd.h>
_Static_assert(sizeof(struct proc_threadinfo)==112,"thread info ABI");
extern int __proc_info(int,int,unsigned int,uint64_t,void*,int);
static int gate[2], ready[2];
static uint64_t worker_tid;
static uint64_t micros(void) { struct timeval t; assert(gettimeofday(&t,NULL)==0); return (uint64_t)t.tv_sec*1000000+t.tv_usec; }
static void *worker(void *unused) {
    (void)unused;
    assert(pthread_setname_np("worker )\nname")==0);
    assert(pthread_threadid_np(NULL,&worker_tid)==0);
    assert(write(ready[1],"x",1)==1);
    char c; assert(read(gate[0],&c,1)==1);
    return NULL;
}
static void untouched(const unsigned char *p,size_t n) { for(size_t i=0;i<n;++i)assert(p[i]==0xa5); }
int main(void) {
    alarm(30);
    assert(pthread_setname_np("main )\nname")==0);
    uint64_t tid; assert(pthread_threadid_np(NULL,&tid)==0);
    struct proc_threadinfo before, after;
    assert(proc_pidinfo(getpid(),PROC_PIDTHREADINFO,tid,&before,sizeof(before))==sizeof(before));
    assert(strcmp(before.pth_name,"main )\nname")==0);
    assert(before.pth_run_state==TH_STATE_RUNNING);
    uint64_t start=micros(); volatile uint64_t work=0;
    do { for(unsigned i=0;i<100000;++i)work+=i; } while(micros()-start<250000);
    assert(proc_pidinfo(getpid(),PROC_PIDTHREADINFO,tid,&after,sizeof(after))==sizeof(after));
    assert(after.pth_user_time>=before.pth_user_time+100000000);
    assert(after.pth_system_time>=before.pth_system_time);
    assert(pipe(gate)==0&&pipe(ready)==0);
    pthread_t thread; assert(pthread_create(&thread,NULL,worker,NULL)==0);
    char c; assert(read(ready[0],&c,1)==1);
    uint64_t other; assert(pthread_threadid_np(thread,&other)==0&&other==worker_tid&&other!=tid);
    /* Wait for the worker to block in read, then observe that target, not self. */
    for(unsigned i=0;i<100;++i) {
        assert(proc_pidinfo(getpid(),PROC_PIDTHREADINFO,other,&after,sizeof(after))==sizeof(after));
        if(after.pth_run_state==TH_STATE_WAITING)break;
        usleep(1000);
    }
    assert(strcmp(after.pth_name,"worker )\nname")==0&&after.pth_run_state==TH_STATE_WAITING);
    assert(after.pth_cpu_usage==0&&after.pth_policy==0&&after.pth_flags==0&&after.pth_sleep_time==0);
    assert(after.pth_curpri==0&&after.pth_priority==0&&after.pth_maxpriority==0);
    unsigned char bytes[4200];
    memset(bytes,0xa5,sizeof(bytes));
    assert(__proc_info(2,getpid(),PROC_PIDTHREADINFO,tid,bytes,111)==-1&&errno==ENOSPC); untouched(bytes,sizeof(bytes));
    assert(__proc_info(2,getpid(),PROC_PIDTHREADINFO,tid,NULL,112)==-1&&errno==EFAULT);
    assert(__proc_info(2,getpid(),PROC_PIDTHREADINFO,tid,bytes,-1)==-1&&errno==EINVAL); untouched(bytes,sizeof(bytes));
    assert(__proc_info(2,getpid(),PROC_PIDTHREADINFO,2147483647,bytes,128)==-1&&errno==ESRCH); untouched(bytes,sizeof(bytes));
    assert(__proc_info(2,2147483647,PROC_PIDTHREADINFO,tid,bytes,128)==-1&&errno==ESRCH); untouched(bytes,sizeof(bytes));
    assert(__proc_info(2,getpid(),PROC_PIDTHREADINFO,tid|(1ull<<32),bytes,128)==112); untouched(bytes+112,sizeof(bytes)-112);
    assert(write(gate[1],"x",1)==1&&pthread_join(thread,NULL)==0);
    memset(bytes,0xa5,sizeof(bytes));
    assert(__proc_info(2,getpid(),PROC_PIDTHREADINFO,other,bytes,128)==-1&&errno==ESRCH); untouched(bytes,sizeof(bytes));
    assert(__proc_info(2,getpid(),PROC_PIDPATHINFO,0,NULL,4096)==-1&&errno==EFAULT);
    assert(__proc_info(2,getpid(),PROC_PIDPATHINFO,0,bytes,-1)==-1&&errno==EINVAL); untouched(bytes,sizeof(bytes));
    assert(__proc_info(2,getpid(),PROC_PIDPATHINFO,0,bytes,1023)==-1&&errno==ENOMEM); untouched(bytes,sizeof(bytes));
    assert(__proc_info(2,getpid(),PROC_PIDPATHINFO,0,bytes,4097)==-1&&errno==EOVERFLOW); untouched(bytes,sizeof(bytes));
    assert(__proc_info(2,2147483647,PROC_PIDPATHINFO,0,bytes,4096)==-1&&errno==ESRCH); untouched(bytes,sizeof(bytes));
    char path[4096]; int length=proc_pidpath(getpid(),path,sizeof(path));
    assert(length>0&&length==(int)strlen(path)&&path[0]=='/'&&strstr(path,"thread-info.c")!=NULL);
    assert(__proc_info(2,getpid(),PROC_PIDPATHINFO,0,bytes,4096)==length);
    assert(memcmp(bytes,path,length+1)==0); untouched(bytes+length+1,sizeof(bytes)-length-1);
    for(unsigned n=0;n<2;++n) {close(gate[n]);close(ready[n]);}
    puts("Dearust thread info and executable path regression checks passed");
    return 0;
}
