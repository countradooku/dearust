/* Nonaborting owned-child observations of original C defects and repaired ABI. */
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
static int probe(unsigned n){
    if(n==0){errno=0;long rc=syscall(SYS_getrlimit,RLIMIT_NOFILE,NULL);return rc==-1 && errno==EFAULT?0:1;}
    if(n==1){errno=0;long rc=syscall(SYS_setrlimit,999,NULL);return rc==-1 && errno==EFAULT?0:1;}
    if(n==2){unsigned char output[144];memset(output,0xa5,sizeof(output));errno=0;long rc=syscall(SYS_getrusage,1,output);return rc==-1 && errno==EINVAL?0:1;}
    if(n==3){
        struct rusage guest;int64_t host[18];long ret;
        assert(getrusage(RUSAGE_SELF,&guest)==0);
        __asm__ volatile("syscall":"=a"(ret):"a"(98L),"D"(0L),"S"(host):"rcx","r11","memory");
        return ret==0 && guest.ru_maxrss==host[4]*1024?0:1;
    }
    struct rlimit l;assert(getrlimit(RLIMIT_CORE,&l)==0);return l.rlim_max==RLIM_INFINITY?0:1;
}
int main(void){
    alarm(60);for(unsigned n=0;n<5;n++){
        pid_t child=fork();assert(child>=0);if(child==0)_exit(probe(n));
        int status;assert(waitpid(child,&status,0)==child);
        printf("resource_probe=%u exit=%d signal=%d\n",n,WIFEXITED(status)?WEXITSTATUS(status):-1,WIFSIGNALED(status)?WTERMSIG(status):0);
    }
    return 0;
}
