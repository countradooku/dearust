/* Native-only adapters: production code still copies all guest pointers safely. */
#define _GNU_SOURCE
#include <assert.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/statfs.h>
#include <sys/syscall.h>
#include <unistd.h>
struct darwin_fs {
    uint32_t bsize; int32_t iosize; uint64_t blocks,bfree,bavail,files,ffree,fsid;
    uint32_t owner,type,flags,subtype; char typename[16],mountpoint[1024],source[1024];
    uint32_t flags_ext,reserved[7];
};
_Static_assert(sizeof(struct darwin_fs)==2168,"Darwin x64 statfs2168");
_Static_assert(offsetof(struct darwin_fs,mountpoint)==88,"mount path offset");
_Static_assert(offsetof(struct darwin_fs,flags_ext)==2136,"extended flags offset");
struct expanded {char path[4096];unsigned flags;int dfd;};
extern long sys_statfs(const char *,void *),sys_statfs64(const char *,void *);
extern long sys_fstatfs(int,void *),sys_fstatfs64(int,void *);
static _Thread_local int thread_wd=AT_FDCWD;
static int force_procfs;
static unsigned blocked_mount_queries;
int dearust_get_perthread_wd(void){return thread_wd;}
int atfd(int fd){return fd==-2?thread_wd:fd;}
__attribute__((weak)) int dearust_copy_signals_ready(void){return 0;}
int vchroot_expand(struct expanded *args){
    assert(args->flags==1 && memchr(args->path,0,sizeof(args->path)));
    if(args->path[0]=='/' || args->dfd==AT_FDCWD)return 0;
    char link[64],base[4096];int count=snprintf(link,sizeof(link),"/proc/self/fd/%d",args->dfd);
    assert(count>0 && (size_t)count<sizeof(link));
    ssize_t n=readlink(link,base,sizeof(base)-1);if(n<0)return -errno;base[n]=0;
    size_t length=strlen(args->path);if((size_t)n+length+2>sizeof(args->path))return -ENAMETOOLONG;
    memmove(args->path+n+1,args->path,length+1);memcpy(args->path,base,(size_t)n);args->path[n]='/';return 0;
}
int vchroot_unexpand(void *args){assert(memchr(args,0,4096));return 0;}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){
    if(force_procfs && (nr==332 || nr==457)){blocked_mount_queries++;return -ENOSYS;}
    long result=syscall(nr,a,b,c,d,e,f);return result<0?-errno:result;
}
static unsigned descriptors(void){
    DIR *directory=opendir("/proc/self/fd");assert(directory);unsigned count=0;struct dirent *entry;
    while((entry=readdir(directory)))if(strcmp(entry->d_name,".") && strcmp(entry->d_name,".."))count++;
    assert(closedir(directory)==0);return count;
}
static uint32_t flags(unsigned long value){
    return ((value&1)?1:0)|((value&2)?8:0)|((value&4)?16:0)|((value&8)?4:0)|
           ((value&16)?2:0)|((value&1024)?0x10000000:0);
}
static void verify(const struct darwin_fs *out,const struct statfs *reference){
    uint64_t fsid;memcpy(&fsid,&reference->f_fsid,sizeof(fsid));
    assert(out->bsize==(uint32_t)(reference->f_frsize?reference->f_frsize:reference->f_bsize) && out->iosize==(int32_t)reference->f_bsize);
    assert(out->blocks==reference->f_blocks && out->files==reference->f_files && out->fsid==fsid);
    assert(out->bfree<=out->blocks && out->bavail<=out->bfree && out->ffree<=out->files);
    assert(out->flags==flags((unsigned long)reference->f_flags));
    assert(out->owner==0 && out->subtype==0 && out->flags_ext==0);
    for(unsigned i=0;i<7;i++)assert(out->reserved[i]==0);
    assert(out->typename[0] && memchr(out->typename,0,sizeof(out->typename)));
    assert(out->mountpoint[0]=='/' && memchr(out->mountpoint,0,sizeof(out->mountpoint)));
    assert(out->source[0] && memchr(out->source,0,sizeof(out->source)));
}
struct output {uint64_t before;struct darwin_fs fs;uint64_t after;};
static void guards(const struct output *out){assert(out->before==UINT64_C(0xa5a5a5a5a5a5a5a5) && out->after==UINT64_C(0xa5a5a5a5a5a5a5a5));}
int main(void){
    force_procfs=getenv("DEARUST_TEST_PROCFS")!=NULL;
    alarm(60);char root[]="/tmp/dearust-filesystem-XXXXXX";assert(mkdtemp(root));
    int directory=open(root,O_RDONLY|O_DIRECTORY);assert(directory>=0);thread_wd=directory;
    int file=openat(directory,"file",O_CREAT|O_EXCL|O_RDWR,0600);assert(file>=0);
    assert(symlinkat("file",directory,"symlink")==0 && mkfifoat(directory,"fifo",0600)==0);
    int fifo=openat(directory,"fifo",O_RDONLY|O_NONBLOCK);assert(fifo>=0);
    struct statfs reference;assert(fstatfs(file,&reference)==0);unsigned initial=descriptors();
    size_t page=(size_t)sysconf(_SC_PAGESIZE);assert(page>=sizeof(struct darwin_fs));
    unsigned char *mapping=mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);assert(mapping!=MAP_FAILED);
    memset(mapping,0xa5,page*2);assert(mprotect(mapping+page,page,PROT_NONE)==0);
    char overlong[4096];memset(overlong,'a',sizeof(overlong));
    for(int wide=0;wide<2;wide++){
        long (*pathcall)(const char *,void *)=wide?sys_statfs64:sys_statfs;
        long (*fdcall)(int,void *)=wide?sys_fstatfs64:sys_fstatfs;
        struct output out;memset(&out,0xa5,sizeof(out));
        assert(pathcall("symlink",&out.fs)==0);guards(&out);verify(&out.fs,&reference);
        assert(fdcall(file,&out.fs)==0);guards(&out);verify(&out.fs,&reference);
        assert(fdcall(fifo,&out.fs)==0);guards(&out);verify(&out.fs,&reference);
        struct output before=out;
        assert(pathcall("missing",NULL)==-2 && fdcall(-42,NULL)==-9);
        assert(pathcall(NULL,&out.fs)==-14 && pathcall((void*)1,&out.fs)==-14);
        assert(pathcall((void*)(mapping+page),&out.fs)==-14 && pathcall(overlong,&out.fs)==-63);
        assert(memcmp(&before,&out,sizeof(out))==0);
        memcpy(mapping+page-5,"file",5);assert(pathcall((char*)mapping+page-5,&out.fs)==0);verify(&out.fs,&reference);guards(&out);
        assert(pathcall("file",NULL)==-14 && fdcall(file,NULL)==-14);
        assert(pathcall("file",mapping+page)==-14 && fdcall(file,mapping+page)==-14);
        assert(pathcall("file",mapping+page-32)==-14 && fdcall(file,mapping+page-32)==-14);
        assert(mapping[page-33]==0xa5);
        assert(mprotect(mapping,page,PROT_READ)==0);
        assert(pathcall("file",mapping)==-14 && fdcall(file,mapping)==-14);
        assert(mprotect(mapping,page,PROT_READ|PROT_WRITE)==0);
        int channel[2];assert(pipe(channel)==0);assert(fdcall(channel[0],&out.fs)==-22 && fdcall(channel[1],&out.fs)==-22);
        assert(close(channel[0])==0 && close(channel[1])==0);
        int sockets[2];assert(socketpair(AF_UNIX,SOCK_STREAM,0,sockets)==0);
        assert(fdcall(sockets[0],&out.fs)==-22);assert(close(sockets[0])==0 && close(sockets[1])==0);
        int event=eventfd(0,EFD_CLOEXEC);assert(event>=0 && fdcall(event,&out.fs)==-22 && close(event)==0);
        const char *mounts[]={"/proc","/sys"};
        for(unsigned i=0;i<2;i++){struct statfs other;assert(statfs(mounts[i],&other)==0 && pathcall(mounts[i],&out.fs)==0);verify(&out.fs,&other);guards(&out);}
        for(int i=0;i<32;i++)assert(pathcall("file",&out.fs)==0 && fdcall(file,&out.fs)==0);
        assert(descriptors()==initial);
    }
    assert(unlinkat(directory,"file",0)==0);
    struct output deleted;memset(&deleted,0xa5,sizeof(deleted));
    assert(sys_fstatfs(file,&deleted.fs)==0 && sys_fstatfs64(file,&deleted.fs)==0);verify(&deleted.fs,&reference);guards(&deleted);
    assert(descriptors()==initial && munmap(mapping,page*2)==0);
    assert(close(file)==0 && close(fifo)==0 && unlinkat(directory,"symlink",0)==0 && unlinkat(directory,"fifo",0)==0);
    assert(close(directory)==0 && rmdir(root)==0);
    if(force_procfs)assert(blocked_mount_queries>0);
    puts("filesystem: four entries, numeric conversion/flags/zero fields, symlinks, TLS cwd, unlinked fd, bounded paths, contained output faults, non-filesystem fd errors and descriptor ownership pass");
}
