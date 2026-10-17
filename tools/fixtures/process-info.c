/* Runtime regression for Dearust BSD/task queries and confirmed legacy defects. */
#include <assert.h>
#include <errno.h>
#include <libproc.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <unistd.h>
_Static_assert(sizeof(struct proc_bsdshortinfo)==64,"short BSD ABI");
_Static_assert(sizeof(struct proc_bsdinfo)==136,"BSD ABI");
_Static_assert(sizeof(struct proc_taskinfo)==96,"task ABI");
_Static_assert(sizeof(struct proc_taskallinfo)==232,"all ABI");
extern int __proc_info(int,int,unsigned int,uint64_t,void*,int);
static uint64_t micros(void) { struct timeval v; assert(gettimeofday(&v,NULL)==0); return (uint64_t)v.tv_sec*1000000+v.tv_usec; }
static void query_credentials(int pid, unsigned uid, unsigned gid) {
    struct proc_bsdshortinfo s;
    assert(proc_pidinfo(pid,PROC_PIDT_SHORTBSDINFO,0,&s,sizeof(s))==sizeof(s));
    assert(s.pbsi_pid==(unsigned)pid&&s.pbsi_uid==uid&&s.pbsi_ruid==uid&&s.pbsi_svuid==uid);
    assert(s.pbsi_gid==gid&&s.pbsi_rgid==gid&&s.pbsi_svgid==gid);
    assert((s.pbsi_flags&PROC_FLAG_LP64)!=0&&(s.pbsi_flags&PROC_FLAG_INEXIT)==0);
    struct proc_bsdinfo b;
    assert(proc_pidinfo(pid,PROC_PIDTBSDINFO,0,&b,sizeof(b))==sizeof(b));
    assert(b.pbi_pid==s.pbsi_pid&&b.pbi_ppid==s.pbsi_ppid&&b.pbi_pgid==s.pbsi_pgid);
    assert(b.pbi_uid==uid&&b.pbi_gid==gid&&b.pbi_ruid==uid&&b.pbi_rgid==gid);
    assert((b.pbi_flags&PROC_FLAG_LP64)!=0&&(b.pbi_flags&PROC_FLAG_INEXIT)==0);
}
int main(void) {
    alarm(30);
    int me=getpid();
    assert(pthread_setname_np("a ) tricky name")==0);
    query_credentials(me,getuid(),getgid());
    struct proc_bsdinfo b;
    assert(proc_pidinfo(me,PROC_PIDTBSDINFO,0,&b,sizeof(b))==sizeof(b));
    assert(strcmp(b.pbi_comm,"a ) tricky name")==0);
    assert(strstr(b.pbi_name,"process-info.c")!=NULL);
    uint64_t now=micros()/1000000;
    assert(b.pbi_start_tvsec<=now&&now-b.pbi_start_tvsec<120&&b.pbi_start_tvusec<1000000);
    int gate[2],ready[2]; assert(pipe(gate)==0&&pipe(ready)==0);
    int child=fork(); assert(child>=0);
    if(child==0) {
        close(gate[1]);close(ready[0]);
        assert(setgid(1235)==0&&setuid(1234)==0);
        assert(write(ready[1],"x",1)==1);char c;assert(read(gate[0],&c,1)==1);
        assert(setgid(2235)==0&&setuid(2234)==0);
        assert(write(ready[1],"x",1)==1);assert(read(gate[0],&c,1)==1);
        _exit(0);
    }
    close(gate[0]);close(ready[1]);char c;assert(read(ready[0],&c,1)==1);
    query_credentials(child,1234,1235);
    assert(write(gate[1],"x",1)==1&&read(ready[0],&c,1)==1);
    query_credentials(child,2234,2235);
    query_credentials(me,getuid(),getgid());
    assert(write(gate[1],"x",1)==1);close(gate[1]);close(ready[0]);
    int status;assert(waitpid(child,&status,0)==child&&WIFEXITED(status)&&WEXITSTATUS(status)==0);
    struct proc_taskinfo before,after;
    assert(proc_pidinfo(me,PROC_PIDTASKINFO,0,&before,sizeof(before))==sizeof(before));
    size_t amount=16*1024*1024;
    volatile unsigned char *memory=mmap(NULL,amount,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(memory!=MAP_FAILED);
    for(size_t i=0;i<amount;i+=4096)memory[i]=42;
    uint64_t started=micros();volatile uint64_t total=0;
    do { for(unsigned i=0;i<100000;++i)total+=i; } while(micros()-started<250000);
    assert(proc_pidinfo(me,PROC_PIDTASKINFO,0,&after,sizeof(after))==sizeof(after));
    assert(after.pti_resident_size>=8*1024*1024&&after.pti_resident_size%4096==0);
    assert(after.pti_resident_size<=after.pti_virtual_size&&after.pti_threadnum>0);
    assert(after.pti_total_user>=before.pti_total_user+100000000);
    assert(after.pti_faults>=before.pti_faults);
    struct proc_taskallinfo all;
    assert(proc_pidinfo(me,PROC_PIDTASKALLINFO,0,&all,sizeof(all))==sizeof(all));
    assert(all.pbsd.pbi_pid==(unsigned)me&&all.ptinfo.pti_resident_size>=8*1024*1024);
    assert(munmap((void*)memory,amount)==0);
    const int flavors[]={PROC_PIDT_SHORTBSDINFO,PROC_PIDTBSDINFO,PROC_PIDTASKINFO,PROC_PIDTASKALLINFO};
    const int sizes[]={64,136,96,232};
    unsigned char bytes[256];
    for(unsigned i=0;i<4;++i) {
        memset(bytes,0xa5,sizeof(bytes));errno=0;
        assert(__proc_info(2,me,flavors[i],0,bytes,sizes[i]-1)==-1&&errno==ENOSPC);
        for(unsigned j=0;j<sizeof(bytes);++j)assert(bytes[j]==0xa5);
        assert(__proc_info(2,me,flavors[i],0,NULL,sizes[i])==-1&&errno==EFAULT);
        assert(__proc_info(2,me,flavors[i],0,bytes,-1)==-1&&errno==EINVAL);
        assert(__proc_info(2,2147483647,flavors[i],0,bytes,sizeof(bytes))==-1&&errno==ESRCH);
        for(unsigned j=0;j<sizeof(bytes);++j)assert(bytes[j]==0xa5);
        assert(__proc_info(2,me,flavors[i],0,bytes,sizeof(bytes))==sizes[i]);
        for(unsigned j=sizes[i];j<sizeof(bytes);++j)assert(bytes[j]==0xa5);
    }
    puts("Dearust BSD and task info regression checks passed");
    return 0;
}
