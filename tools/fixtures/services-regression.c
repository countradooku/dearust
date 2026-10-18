/* Actual Darwin entropy and current-thread services; raw syscall ABI. */
#include <assert.h>
#include <errno.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
static uint64_t current_thread(void){
    uint64_t expected=0;assert(pthread_threadid_np(NULL,&expected)==0 && expected>0);
    long actual=syscall(SYS_thread_selfid);assert(actual>0 && (uint64_t)actual==expected);return expected;
}
static void entropy(void){
    unsigned char bytes[258];memset(bytes,0xa5,sizeof(bytes));
    assert(syscall(SYS_getentropy,bytes+1,256)==0 && bytes[0]==0xa5 && bytes[257]==0xa5);
    memset(bytes,0xa5,sizeof(bytes));assert(syscall(SYS_getentropy,bytes+1,32)==0);
    assert(bytes[0]==0xa5);for(unsigned i=33;i<sizeof(bytes);i++)assert(bytes[i]==0xa5);
}
static pthread_mutex_t lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t condition=PTHREAD_COND_INITIALIZER;
static unsigned ready;
static uint64_t tids[4];
static void *worker(void *arg){
    unsigned index=(unsigned)(uintptr_t)arg;tids[index]=current_thread();
    assert(pthread_mutex_lock(&lock)==0);ready++;assert(pthread_cond_broadcast(&condition)==0);
    while(ready<4)assert(pthread_cond_wait(&condition,&lock)==0);
    assert(pthread_mutex_unlock(&lock)==0);
    for(unsigned i=0;i<256;i++){entropy();assert(current_thread()==tids[index]);}return NULL;
}
int main(void){
    alarm(60);uint64_t parent=current_thread();entropy();
    const size_t sizes[]={257,UINT64_C(0x100000000),SIZE_MAX};
    unsigned char bytes[258];memset(bytes,0xa5,sizeof(bytes));
    for(unsigned i=0;i<sizeof(sizes)/sizeof(sizes[0]);i++){
        errno=0;assert(syscall(SYS_getentropy,bytes+1,sizes[i])==-1 && errno==EINVAL);
        errno=0;assert(syscall(SYS_getentropy,(void*)1,sizes[i])==-1 && errno==EINVAL);
    }
    for(unsigned i=0;i<sizeof(bytes);i++)assert(bytes[i]==0xa5);
    assert(syscall(SYS_getentropy,NULL,0)==0 && syscall(SYS_getentropy,(void*)1,0)==0);
    errno=0;assert(syscall(SYS_getentropy,NULL,1)==-1 && errno==EFAULT);
    long page=sysconf(_SC_PAGESIZE);assert(page>256);
    unsigned char *mapping=mmap(NULL,(size_t)page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);assert(mapping!=MAP_FAILED);
    memset(mapping,0xa5,(size_t)page*2);assert(mprotect(mapping+page,(size_t)page,PROT_NONE)==0);
    assert(syscall(SYS_getentropy,mapping+page,0)==0);
    errno=0;assert(syscall(SYS_getentropy,mapping+page,1)==-1 && errno==EFAULT);
    errno=0;assert(syscall(SYS_getentropy,mapping+page-16,32)==-1 && errno==EFAULT);
    assert(mapping[page-17]==0xa5);
    assert(mprotect(mapping+page,(size_t)page,PROT_READ)==0);
    errno=0;assert(syscall(SYS_getentropy,mapping+page,32)==-1 && errno==EFAULT);
    for(unsigned i=0;i<256;i++)assert(mapping[page+i]==0xa5);
    assert(munmap(mapping,(size_t)page*2)==0);
    pthread_t threads[4];for(unsigned i=0;i<4;i++)assert(pthread_create(&threads[i],NULL,worker,(void*)(uintptr_t)i)==0);
    for(unsigned i=0;i<4;i++)assert(pthread_join(threads[i],NULL)==0);
    for(unsigned i=0;i<4;i++){assert(tids[i]!=parent);for(unsigned j=0;j<i;j++)assert(tids[i]!=tids[j]);}
    int channel[2];assert(pipe(channel)==0);pid_t child=fork();assert(child>=0);
    if(child==0){assert(close(channel[0])==0);uint64_t tid=current_thread();assert(tid!=parent);entropy();assert(write(channel[1],&tid,sizeof(tid))==sizeof(tid));_exit(0);}
    assert(close(channel[1])==0);uint64_t child_tid=0;assert(read(channel[0],&child_tid,sizeof(child_tid))==sizeof(child_tid) && child_tid>0 && child_tid!=parent);
    assert(close(channel[0])==0);int status;assert(waitpid(child,&status,0)==child && WIFEXITED(status) && WEXITSTATUS(status)==0);
    assert(current_thread()==parent);puts("Dearust entropy and thread services regression checks passed");return 0;
}
