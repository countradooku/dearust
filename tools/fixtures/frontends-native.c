/* Native-only filesystem/TLS/vchroot adapters. No production fallback symbols. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
#include "guest-memory-native.h"

struct expanded { char path[4096]; unsigned flags; int dfd; };
extern long sys_readlink(const char *, char *, int32_t);
extern long sys_symlink(const char *, const char *);
extern long sys_link(const char *, const char *);
extern long sys_unlink(const char *);
extern long sys_rename(const char *, const char *);
extern long sys_mkdir(const char *, uint32_t);
enum operation { READLINK, SYMLINK, LINK, UNLINK, RENAME, MKDIR };
struct context {
    int current, canonical, other, captured, resolved, mutate;
    int tls, atcalls, expansions, flags, syscalls, expansion_fail, inject;
    enum operation op;
    uint32_t mode;
    int32_t count;
    const char *target;
    char *output;
};
static _Thread_local struct context ctx;

int dearust_get_perthread_wd(void) {
    assert(ctx.tls == 0 && ctx.atcalls == 0 && ctx.expansions == 0 && ctx.syscalls == 0);
    ++ctx.tls;
    ctx.captured = ctx.current;
    ctx.resolved = ctx.current == -2 ? ctx.canonical : ctx.current;
    if (ctx.mutate) ctx.current = ctx.other;
    return ctx.captured;
}
int atfd(int fd) {
    assert(ctx.tls == 1 && fd == ctx.captured);
    ++ctx.atcalls;
    return fd == -2 ? ctx.canonical : fd;
}
int vchroot_expand(struct expanded *args) {
    assert(ctx.tls == 1 && args->dfd == ctx.resolved && args->flags == 0);
    assert(memchr(args->path, 0, sizeof(args->path)));
    ++ctx.expansions;
    return ctx.expansion_fail == ctx.expansions ? -36 : 0;
}
int vchroot_expand_at(struct expanded *args) { return vchroot_expand(args); }
int atflags_bsd_to_linux(int flags) {
    assert(ctx.tls == 1 && flags == 0);
    assert(ctx.op == LINK || ctx.op == UNLINK);
    assert(ctx.expansions == (ctx.op == LINK ? 2 : 1));
    ++ctx.flags;
    return 0;
}
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    long memory_result;
    if (dearust_native_memory_syscall(a,b,c,d,e,f,nr,&memory_result)) return memory_result;
    assert(ctx.tls == 1 && f == 0);
    ++ctx.syscalls;
    switch (ctx.op) {
    case READLINK:
        assert(nr == SYS_readlinkat && a == ctx.resolved && b && e == 0);
        /* A zero-length Darwin readlink still validates its path by reading
         * into private scratch, without touching the supplied guest address. */
        if (ctx.count != 0) {
            assert(c == (long)ctx.output && d == (long)ctx.count);
        } else {
            assert(c != (long)ctx.output && d > 0);
        }
        break;
    case SYMLINK:
        assert(nr == SYS_symlinkat && a == (long)ctx.target && b == ctx.resolved && c);
        assert(d == 0 && e == 0);
        break;
    case LINK:
        assert(nr == SYS_linkat && a == ctx.resolved && c == ctx.resolved && b && d);
        assert(e == 0 && ctx.flags == 1);
        break;
    case UNLINK:
        assert(nr == SYS_unlinkat && a == ctx.resolved && b && c == 0 && d == 0 && e == 0);
        assert(ctx.flags == 1);
        break;
    case RENAME:
        assert(nr == SYS_renameat && a == ctx.resolved && c == ctx.resolved && b && d && e == 0);
        break;
    case MKDIR:
        assert(nr == SYS_mkdirat && a == ctx.resolved && b && c == (long)ctx.mode);
        assert(d == 0 && e == 0);
        break;
    }
    if (ctx.inject) return -EIO;
    long result = syscall(nr, a,b,c,d,e,f);
    return result < 0 ? -errno : result;
}
static void begin(enum operation op, int fd) {
    int canonical = ctx.canonical, other = ctx.other;
    ctx = (struct context){ .op=op, .current=fd, .canonical=canonical, .other=other, .mutate=1 };
}
static void complete(void) {
    int paths = ctx.op == LINK || ctx.op == RENAME ? 2 : 1;
    assert(ctx.tls == 1 && ctx.atcalls == paths && ctx.expansions == paths && ctx.syscalls == 1);
    assert(ctx.current == ctx.other);
}
static long readlink_call(int fd, const char *path, char *output, int32_t count) {
    begin(READLINK,fd); ctx.output=output; ctx.count=count;
    long result=sys_readlink(path,output,count); assert(ctx.tls==1); return result;
}
static long symlink_call(int fd, const char *target, const char *path) {
    begin(SYMLINK,fd); ctx.target=target;
    long result=sys_symlink(target,path); assert(ctx.tls==1); return result;
}
static long link_call(int fd, const char *source, const char *dest) {
    begin(LINK,fd); long result=sys_link(source,dest); assert(ctx.tls==1); return result;
}
static long unlink_call(int fd, const char *path) {
    begin(UNLINK,fd); long result=sys_unlink(path); assert(ctx.tls==1); return result;
}
static long rename_call(int fd, const char *source, const char *dest) {
    begin(RENAME,fd); long result=sys_rename(source,dest); assert(ctx.tls==1); return result;
}
static long mkdir_call(int fd, const char *path, uint32_t mode) {
    begin(MKDIR,fd); ctx.mode=mode;
    long result=sys_mkdir(path,mode); assert(ctx.tls==1); return result;
}
static struct stat info(int fd,const char *path) {
    struct stat st; assert(fstatat(fd,path,&st,AT_SYMLINK_NOFOLLOW)==0); return st;
}
static void make_file(int fd,const char *path) {
    int file=openat(fd,path,O_CREAT|O_EXCL|O_WRONLY,0600); assert(file>=0); assert(close(file)==0);
}
static long invoke(enum operation op, const char *first, const char *second) {
    switch(op) {
    case READLINK: return sys_readlink(first,ctx.output,ctx.count);
    case SYMLINK: ctx.target=first; return sys_symlink(first,second);
    case LINK: return sys_link(first,second);
    case UNLINK: return sys_unlink(first);
    case RENAME: return sys_rename(first,second);
    case MKDIR: return sys_mkdir(first,ctx.mode);
    }
    abort();
}
static void invalid_inputs(int fd) {
    char overlong[4096]; memset(overlong,'x',sizeof(overlong));
    size_t page=(size_t)sysconf(_SC_PAGESIZE);
    void *protected=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    assert(protected!=MAP_FAILED);
    for(int operation=READLINK;operation<=MKDIR;++operation) {
        enum operation op=(enum operation)operation;
        /* For symlink the copied path is the second argument; target bytes are
         * passed unchanged to Linux. All six entries still read TLS first. */
        for(int which=0;which<3;++which) {
            const char *bad=which==0?NULL:which==1?(const char *)protected:overlong;
            begin(op,fd); ctx.count=8;
            const char *first=op==SYMLINK?"raw-target":bad;
            const char *second=op==SYMLINK?bad:"never-created";
            assert(invoke(op,first,second)==(which==2?-63:-14));
            assert(ctx.tls==1 && ctx.expansions==0 && ctx.syscalls==0);
            if(op==LINK || op==RENAME) {
                begin(op,fd); assert(invoke(op,"file",bad)==(which==2?-63:-14));
                assert(ctx.tls==1 && ctx.expansions==0 && ctx.syscalls==0);
            }
        }
        begin(op,fd); ctx.count=8; ctx.expansion_fail=1;
        assert(invoke(op,"sym","never-created")==-63);
        assert(ctx.tls==1 && ctx.expansions==1 && ctx.syscalls==0);
        if(op==LINK || op==RENAME) {
            begin(op,fd); ctx.expansion_fail=2;
            assert(invoke(op,"file","never-created")==-63);
            assert(ctx.expansions==2 && ctx.syscalls==0);
        }
        begin(op,fd); ctx.count=8; ctx.inject=1; ctx.mode=UINT32_MAX;
        assert(invoke(op,"sym","never-created")==-5); complete();
        begin(op,-42); ctx.count=8;
        assert(invoke(op,"sym","never-created")==-9); complete();
    }
    assert(munmap(protected,page)==0);
}
struct worker { int fd, other; pthread_barrier_t *barrier; };
static void *thread_work(void *opaque) {
    struct worker *worker=opaque;
    ctx.canonical=worker->fd; ctx.other=worker->other;
    int wait=pthread_barrier_wait(worker->barrier); assert(wait==0 || wait==PTHREAD_BARRIER_SERIAL_THREAD);
    for(int i=0;i<100;++i) {
        char out[8]; memset(out,0x55,sizeof(out));
        assert(mkdir_call(-2,"directory",0700)==0); complete();
        assert(symlink_call(-2,"../\xff","sym")==0); complete();
        assert(readlink_call(-2,"sym",out,3)==3); complete();
        assert(memcmp(out,"../",3)==0 && out[3]==0x55);
        assert(link_call(-2,"sym","hard")==0); complete();
        struct stat before=info(worker->fd,"sym"), after=info(worker->fd,"hard");
        assert(before.st_ino==after.st_ino && before.st_nlink==2 && S_ISLNK(before.st_mode));
        assert(rename_call(-2,"hard","renamed")==0); complete();
        assert(unlink_call(-2,"sym")==0); complete();
        assert(info(worker->fd,"renamed").st_nlink==1);
        assert(unlink_call(-2,"renamed")==0); complete();
        assert(unlinkat(worker->fd,"directory",AT_REMOVEDIR)==0);
    }
    return NULL;
}
int main(void) {
    char directory[]="/tmp/dearust-frontends-XXXXXX"; assert(mkdtemp(directory));
    int base=open(directory,O_RDONLY|O_DIRECTORY); assert(base>=0);
    assert(mkdirat(base,"first",0700)==0 && mkdirat(base,"second",0700)==0);
    int first=openat(base,"first",O_RDONLY|O_DIRECTORY), second=openat(base,"second",O_RDONLY|O_DIRECTORY);
    assert(first>=0 && second>=0);
    ctx.canonical=first; ctx.other=second;
    make_file(first,"file");
    const char target[]="../unresolved/\xff-target";
    assert(symlink_call(first,target,"sym")==0); complete();
    assert(S_ISLNK(info(first,"sym").st_mode));
    char output[128]; memset(output,0x55,sizeof(output));
    assert(readlink_call(first,"sym",output,sizeof(output))==(long)strlen(target)); complete();
    assert(memcmp(output,target,strlen(target))==0 && output[strlen(target)]==0x55);
    assert(readlink_call(first,"sym",output,3)==3); complete(); assert(output[3]==target[3]);
    memset(output,0x55,sizeof(output));
    assert(readlink_call(first,"sym",output,3)==3); complete();
    assert(memcmp(output,target,3)==0 && output[3]==0x55);
    size_t page=(size_t)sysconf(_SC_PAGESIZE);
    char *protected=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0); assert(protected!=MAP_FAILED);
    assert(readlink_call(first,"sym",protected,0)==0); complete();
    assert(readlink_call(first,"sym",NULL,0)==0); complete();
    assert(readlink_call(first,"missing",protected,0)==-2); complete();
    assert(readlink_call(first,"file",protected,0)==-22); complete();
    assert(readlink_call(first,"sym",protected,8)==-14); complete();
    assert(readlink_call(first,"missing",NULL,8)==-2); complete();
    assert(readlink_call(first,"sym",NULL,8)==-14); complete();
    assert(readlink_call(first,"sym",output,INT32_MAX)==(long)strlen(target)); complete();
    assert(readlink_call(first,"sym",output,-1)==-22);
    assert(ctx.tls==1 && ctx.atcalls==0 && ctx.expansions==0 && ctx.syscalls==0);
    assert(munmap(protected,page)==0);
    assert(symlink_call(first,NULL,"invalid-target")==-14); complete();
    protected=mmap(NULL,page,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0); assert(protected!=MAP_FAILED);
    assert(symlink_call(first,protected,"protected-target")==-14); complete();
    assert(munmap(protected,page)==0);
    assert(link_call(first,"file","hard")==0); complete();
    assert(info(first,"file").st_ino==info(first,"hard").st_ino && info(first,"file").st_nlink==2);
    assert(rename_call(first,"hard","renamed")==0); complete();
    assert(faccessat(first,"hard",F_OK,0)==-1 && errno==ENOENT);
    assert(unlink_call(first,"file")==0); complete(); assert(info(first,"renamed").st_nlink==1);
    assert(unlink_call(first,"renamed")==0); complete();
    assert(unlink_call(first,"missing")==-2); complete();
    assert(rename_call(first,"missing","nowhere")==-2); complete();
    assert(link_call(first,"missing","nowhere")==-2); complete();
    mode_t oldmask=umask(0027);
    assert(mkdir_call(first,"mode",0777)==0); complete(); assert((info(first,"mode").st_mode&0777)==0750);
    assert(mkdir_call(first,"mode",0777)==-17); complete();
    assert(mkdir_call(first,"missing/child",UINT32_MAX)==-2); complete();
    assert(mkdir_call(first,"wide-mode",UINT32_MAX)==0); complete();
    struct stat mode=info(first,"wide-mode");
    assert((mode.st_mode&0777)==0750);
    umask(oldmask);
    assert(unlink_call(first,"mode")==-21); complete();
    invalid_inputs(first);
    assert(unlinkat(first,"sym",0)==0 && unlinkat(first,"mode",AT_REMOVEDIR)==0 && unlinkat(first,"wide-mode",AT_REMOVEDIR)==0);
    /* Change the canonical per-thread directory without changing process cwd. */
    ctx.canonical=first; assert(mkdir_call(-2,"first-fresh",0700)==0); complete();
    assert(faccessat(second,"first-fresh",F_OK,0)==-1 && errno==ENOENT);
    assert(unlinkat(first,"first-fresh",AT_REMOVEDIR)==0);
    ctx.canonical=second; assert(mkdir_call(-2,"fresh",0700)==0); complete();
    assert(faccessat(first,"fresh",F_OK,0)==-1 && errno==ENOENT);
    assert(unlinkat(second,"fresh",AT_REMOVEDIR)==0);
    pthread_barrier_t barrier; assert(pthread_barrier_init(&barrier,NULL,2)==0);
    struct worker workers[2]={{first,second,&barrier},{second,first,&barrier}};
    pthread_t threads[2];
    for(int i=0;i<2;++i) assert(pthread_create(&threads[i],NULL,thread_work,&workers[i])==0);
    for(int i=0;i<2;++i) assert(pthread_join(threads[i],NULL)==0);
    assert(pthread_barrier_destroy(&barrier)==0);
    assert(close(first)==0 && close(second)==0);
    assert(unlinkat(base,"first",AT_REMOVEDIR)==0 && unlinkat(base,"second",AT_REMOVEDIR)==0);
    assert(close(base)==0 && rmdir(directory)==0);
    puts("frontends: six TLS-first entries, one snapshot, independent concurrent cwd, real filesystem/errors, raw targets, widths and fault-contained inputs");
}
