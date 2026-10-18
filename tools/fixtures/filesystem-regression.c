/* Actual Darwin x64 statfs syscall layouts and guest pointer-fault semantics. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
extern int pthread_fchdir_np(int);
_Static_assert(sizeof(struct statfs)==2168,"Darwin x64 statfs2168");
_Static_assert(offsetof(struct statfs,f_mntonname)==88,"mount path offset");
_Static_assert(offsetof(struct statfs,f_flags_ext)==2136,"extended flags offset");
struct output {uint64_t before;struct statfs fs;uint64_t after;};
static void valid(const struct output *out){
    const struct statfs *fs=&out->fs;
    assert(out->before==UINT64_C(0xa5a5a5a5a5a5a5a5) && out->after==UINT64_C(0xa5a5a5a5a5a5a5a5));
    assert(fs->f_bsize>0 && fs->f_iosize>0 && fs->f_bfree<=fs->f_blocks && fs->f_bavail<=fs->f_bfree && fs->f_ffree<=fs->f_files);
    assert(fs->f_fstypename[0] && memchr(fs->f_fstypename,0,sizeof(fs->f_fstypename)));
    assert(fs->f_mntonname[0]=='/' && memchr(fs->f_mntonname,0,sizeof(fs->f_mntonname)));
    assert(fs->f_mntfromname[0] && memchr(fs->f_mntfromname,0,sizeof(fs->f_mntfromname)));
    assert(fs->f_owner==0 && fs->f_fssubtype==0 && fs->f_flags_ext==0);
    for(unsigned i=0;i<7;i++)assert(fs->f_reserved[i]==0);
}
static void consistent(const struct statfs *a,const struct statfs *b){
    assert(a->f_bsize==b->f_bsize && a->f_blocks==b->f_blocks && a->f_files==b->f_files);
    assert(memcmp(&a->f_fsid,&b->f_fsid,sizeof(a->f_fsid))==0);
    assert(a->f_type==b->f_type && a->f_flags==b->f_flags);
    assert(strcmp(a->f_fstypename,b->f_fstypename)==0 && strcmp(a->f_mntonname,b->f_mntonname)==0 && strcmp(a->f_mntfromname,b->f_mntfromname)==0);
}
int main(void){
    alarm(60);char root[]="/private/tmp/dearust-filesystem-XXXXXX";assert(mkdtemp(root));
    int directory=open(root,O_RDONLY);assert(directory>=0 && pthread_fchdir_np(directory)==0);
    int file=openat(directory,"file",O_CREAT|O_EXCL|O_RDWR,0600);assert(file>=0);
    char fifo_path[4096];int length=snprintf(fifo_path,sizeof(fifo_path),"%s/fifo",root);assert(length>0 && (size_t)length<sizeof(fifo_path));
    assert(symlinkat("file",directory,"symlink")==0 && mkfifo(fifo_path,0600)==0);
    int fifo=openat(directory,"fifo",O_RDONLY|O_NONBLOCK);assert(fifo>=0);
    struct output reference;memset(&reference,0xa5,sizeof(reference));
    assert(syscall(SYS_fstatfs64,file,&reference.fs)==0);valid(&reference);
    size_t page=(size_t)sysconf(_SC_PAGESIZE);assert(page>=sizeof(struct statfs));
    unsigned char *mapping=mmap(NULL,page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);assert(mapping!=MAP_FAILED);
    memset(mapping,0xa5,page*2);assert(mprotect(mapping+page,page,PROT_NONE)==0);
    char overlong[4096];memset(overlong,'a',sizeof(overlong));
    for(int wide=0;wide<2;wide++){
        int pathnr=wide?SYS_statfs64:SYS_statfs,fdnr=wide?SYS_fstatfs64:SYS_fstatfs;
        struct output out;memset(&out,0xa5,sizeof(out));
        assert(syscall(pathnr,"symlink",&out.fs)==0);valid(&out);consistent(&reference.fs,&out.fs);
        assert(syscall(fdnr,file,&out.fs)==0);valid(&out);consistent(&reference.fs,&out.fs);
        assert(syscall(fdnr,fifo,&out.fs)==0);valid(&out);consistent(&reference.fs,&out.fs);
        struct output before=out;
        errno=0;assert(syscall(pathnr,"missing",NULL)==-1 && errno==ENOENT);
        errno=0;assert(syscall(fdnr,-42,NULL)==-1 && errno==EBADF);
        const char *bad[]={NULL,(void*)1,(void*)(mapping+page),overlong};
        for(unsigned i=0;i<4;i++){errno=0;assert(syscall(pathnr,bad[i],&out.fs)==-1 && errno==(i==3?ENAMETOOLONG:EFAULT));}
        assert(memcmp(&before,&out,sizeof(out))==0);
        memcpy(mapping+page-5,"file",5);assert(syscall(pathnr,(char*)mapping+page-5,&out.fs)==0);valid(&out);consistent(&reference.fs,&out.fs);
        void *destinations[]={NULL,mapping+page,mapping+page-32};
        for(unsigned i=0;i<3;i++){
            errno=0;assert(syscall(pathnr,"file",destinations[i])==-1 && errno==EFAULT);
            errno=0;assert(syscall(fdnr,file,destinations[i])==-1 && errno==EFAULT);
        }
        assert(mapping[page-33]==0xa5 && mprotect(mapping,page,PROT_READ)==0);
        errno=0;assert(syscall(pathnr,"file",mapping)==-1 && errno==EFAULT);
        errno=0;assert(syscall(fdnr,file,mapping)==-1 && errno==EFAULT);
        assert(mprotect(mapping,page,PROT_READ|PROT_WRITE)==0);
        int channel[2];assert(pipe(channel)==0);
        errno=0;assert(syscall(fdnr,channel[0],&out.fs)==-1 && errno==EINVAL);
        assert(close(channel[0])==0 && close(channel[1])==0);
        int sockets[2];assert(socketpair(AF_UNIX,SOCK_STREAM,0,sockets)==0);
        errno=0;assert(syscall(fdnr,sockets[0],&out.fs)==-1 && errno==EINVAL);
        assert(close(sockets[0])==0 && close(sockets[1])==0);
    }
    assert(unlinkat(directory,"file",0)==0);
    struct output deleted;memset(&deleted,0xa5,sizeof(deleted));
    assert(syscall(SYS_fstatfs,file,&deleted.fs)==0 && syscall(SYS_fstatfs64,file,&deleted.fs)==0);valid(&deleted);consistent(&reference.fs,&deleted.fs);
    assert(munmap(mapping,page*2)==0 && close(file)==0 && close(fifo)==0);
    assert(unlinkat(directory,"symlink",0)==0 && unlinkat(directory,"fifo",0)==0 && pthread_fchdir_np(-1)==0 && close(directory)==0 && rmdir(root)==0);
    puts("Dearust filesystem statistics regression checks passed");return 0;
}
