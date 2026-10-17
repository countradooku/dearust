/* Regression probe for the Rust process syscall slice, linked against real libproc. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <libproc.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/mman.h>
#include <unistd.h>
_Static_assert(sizeof(struct vinfo_stat)==136,"vinfo_stat ABI");
_Static_assert(sizeof(struct vnode_info)==152,"vnode_info ABI");
_Static_assert(sizeof(struct vnode_info_path)==1176,"vnode_info_path ABI");
_Static_assert(sizeof(struct vnode_fdinfo)==176,"vnode_fdinfo ABI");
_Static_assert(sizeof(struct vnode_fdinfowithpath)==1200,"vnode_fdinfowithpath ABI");
_Static_assert(sizeof(struct proc_vnodepathinfo)==2352,"proc_vnodepathinfo ABI");
extern int __proc_info(int,int,unsigned int,uint64_t,void*,int);
static int contains(const int* p,int bytes,int pid){ for(int i=0;i<bytes/(int)sizeof(int);++i)if(p[i]==pid)return 1;return 0; }
int main(void){
    int pids[4096];int me=getpid();
    assert(proc_listpids(PROC_ALL_PIDS,0,NULL,0)>0);
    int n=proc_listpids(PROC_ALL_PIDS,0,pids,sizeof(pids));assert(n>0&&n%4==0&&contains(pids,n,me));
    n=proc_listpids(PROC_UID_ONLY,getuid(),pids,sizeof(pids));assert(n>0&&contains(pids,n,me));
    n=proc_listpids(PROC_UID_ONLY,(uint32_t)-2,pids,sizeof(pids));assert(!contains(pids,n,me));
    n=proc_listpids(PROC_RUID_ONLY,getuid(),pids,sizeof(pids));assert(n>0&&contains(pids,n,me));
    n=proc_listpids(PROC_PGRP_ONLY,getpgrp(),pids,sizeof(pids));assert(n>0&&contains(pids,n,me));
    n=proc_listpids(PROC_PPID_ONLY,getppid(),pids,sizeof(pids));assert(n>0&&contains(pids,n,me));
    errno=0;assert(__proc_info(1,99,0,0,pids,sizeof(pids))==-1&&errno==EINVAL);
    struct {unsigned char b[7];unsigned char canary[8];} tiny;memset(&tiny,0xa5,sizeof(tiny));
    assert(__proc_info(2,me,PROC_PIDLISTTHREADS,0,tiny.b,7)==0);
    for(unsigned int i=0;i<sizeof(tiny);++i)assert(((unsigned char*)&tiny)[i]==0xa5);
    assert(__proc_info(2,me,PROC_PIDLISTTHREADS,0,tiny.b,-1)==-1&&errno==EINVAL);
    uint64_t threads[256];n=proc_pidinfo(me,PROC_PIDLISTTHREADS,0,threads,sizeof(threads));assert(n>=8&&n%8==0);
    memset(&tiny,0xa5,sizeof(tiny));assert(proc_listpids(PROC_ALL_PIDS,0,tiny.b,3)==0);
    for(unsigned int i=0;i<sizeof(tiny);++i)assert(((unsigned char*)&tiny)[i]==0xa5);
    char temp[]="/tmp/rust-process-query-XXXXXX";int fd=mkstemp(temp);assert(fd>=0);assert(write(fd,"hello",5)==5);
    struct stat sb;assert(fstat(fd,&sb)==0);
    struct vnode_fdinfo vi;memset(&vi,0xcc,sizeof(vi));assert(proc_pidfdinfo(me,fd,PROC_PIDFDVNODEINFO,&vi,sizeof(vi))==sizeof(vi));
    assert(vi.pvi.vi_stat.vst_dev==(uint32_t)sb.st_dev&&vi.pvi.vi_stat.vst_ino==sb.st_ino&&vi.pvi.vi_stat.vst_size==5&&vi.pfi.fi_offset==5);
    struct vnode_fdinfowithpath vip;assert(proc_pidfdinfo(me,fd,PROC_PIDFDVNODEPATHINFO,&vip,sizeof(vip))==sizeof(vip));assert(strstr(vip.pvip.vip_path,"rust-process-query-")!=NULL);
    struct proc_fdinfo fds[4096];n=proc_pidinfo(me,PROC_PIDLISTFDS,0,fds,sizeof(fds));assert(n>0&&n%sizeof(*fds)==0);int found=0;for(int i=0;i<n/(int)sizeof(*fds);++i)if(fds[i].proc_fd==fd){assert(fds[i].proc_fdtype==PROX_FDTYPE_VNODE);found=1;}assert(found);
    n=proc_listpidspath(PROC_ALL_PIDS,0,temp,0,pids,sizeof(pids));assert(n>=4&&contains(pids,n,me));
    struct proc_vnodepathinfo dirs;assert(proc_pidinfo(me,PROC_PIDVNODEPATHINFO,0,&dirs,sizeof(dirs))==sizeof(dirs));assert(dirs.pvi_cdir.vip_vi.vi_stat.vst_ino!=0);
    assert((vi.pfi.fi_openflags & 3)==3);
    assert(vi.pvi.vi_stat.vst_uid==sb.st_uid&&vi.pvi.vi_stat.vst_gid==sb.st_gid);
    void *mapping=mmap(NULL,4096,PROT_READ,MAP_PRIVATE,fd,0);assert(mapping!=MAP_FAILED);
    struct proc_regionwithpathinfo region;assert(proc_pidinfo(me,22,(uint64_t)mapping,&region,sizeof(region))==sizeof(region));
    assert(region.prp_vip.vip_vi.vi_stat.vst_ino==sb.st_ino&&region.prp_vip.vip_vi.vi_stat.vst_dev==(uint32_t)sb.st_dev);
    assert(close(fd)==0);
    n=proc_listpidspath(PROC_ALL_PIDS,0,temp,0,pids,sizeof(pids));assert(n>=4&&contains(pids,n,me));
    assert(munmap(mapping,4096)==0);assert(unlink(temp)==0);
    puts("Rust process query regression checks passed");return 0;
}
