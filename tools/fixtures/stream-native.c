/* GPL-3.0-or-later. Native ABI/regressions for grouped stream operations. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <termios.h>
#include <unistd.h>

extern long sys_read(int, void *, size_t), sys_read_nocancel(int, void *, size_t);
extern long sys_write(int, const void *, size_t), sys_write_nocancel(int, const void *, size_t);
extern long __write_for_xtrace(int, const void *, size_t);
static _Thread_local int injected, syscall_calls, cancel_calls, ioctl_calls;
static _Thread_local long cancel_result = 1, raw_result, expected[6];
static _Thread_local int expected_nr, reject_io, stat_mode, stat_calls;
static int concurrent_ioctl;
static pthread_barrier_t ioctl_barrier;
static void *ioctl_buffers[2];
static _Thread_local int worker_index;
static int ioctl_mode;

long sys_pthread_canceled(int action) {
    assert(action == 0); cancel_calls++; return cancel_result;
}
struct xnu_rlimit { uint64_t current, maximum; long reserved[20]; };
long sys_getrlimit(unsigned int which, struct xnu_rlimit *value) {
    assert(which == 8); struct rlimit real;
    assert(getrlimit(RLIMIT_NOFILE, &real) == 0);
    value->current = real.rlim_cur - 1; value->maximum = real.rlim_max - 1;
    return 0;
}
long linux_syscall(long a, long b, long c, long d, long e, long f, int nr) {
    syscall_calls++;
    if(reject_io) assert(nr == SYS_fcntl && b == F_GETFL && c == 0 && d == 0 && e == 0 && f == 0);
    if (injected && nr==SYS_fstat) {
        stat_calls++;
        assert(a==expected[0] && b && c==0 && d==0 && e==0 && f==0);
        if(stat_mode==4) return -9;
        if(stat_mode) {
            unsigned char *storage=(void *)(uintptr_t)b;
            memset(storage,0,144);
            uint32_t mode=stat_mode==3 ? S_IFREG : S_IFCHR;
            uint64_t device=stat_mode==2 ? UINT64_C(3)<<8 : UINT64_C(2)<<8;
            if(stat_mode==5) device |= UINT64_C(1)<<44;
            memcpy(storage+24,&mode,4); memcpy(storage+40,&device,8);
            return 0;
        }
        long value=syscall(nr,a,b,c,d,e,f); return value==-1 ? -errno : value;
    }
    if (injected) {
        long actual[6] = {a,b,c,d,e,f};
        assert(nr == expected_nr && memcmp(actual, expected, sizeof(actual)) == 0);
        return raw_result;
    }
    long value = syscall(nr,a,b,c,d,e,f);
    return value == -1 ? -errno : value;
}
long __real_ioctl(int fd, int request, void *buffer) {
    ioctl_calls++; assert(request == (int)UINT32_C(0x80045430));
    if (concurrent_ioctl) {
        ioctl_buffers[worker_index] = buffer;
        int b = pthread_barrier_wait(&ioctl_barrier);
        assert(b == 0 || b == PTHREAD_BARRIER_SERIAL_THREAD);
        assert(ioctl_buffers[0] != ioctl_buffers[1]);
        /* Linux TIOCGPTN writes one unsigned 32-bit master PTY number. */
        memset(buffer, 0xa0 + worker_index, 4);
        b = pthread_barrier_wait(&ioctl_barrier);
        assert(b == 0 || b == PTHREAD_BARRIER_SERIAL_THREAD);
        for (int i=0;i<4;i++) assert(((unsigned char *)buffer)[i] == 0xa0 + worker_index);
        return 0;
    }
    if (ioctl_mode) return ioctl_mode == 1 ? 0 : -25;
    long value = syscall(SYS_ioctl, fd, request, buffer);
    return value == -1 ? -errno : value;
}
static long invoke(int index, int fd, void *p, size_t n) {
    switch(index) {
        case 0: return sys_read(fd,p,n);
        case 1: return sys_read_nocancel(fd,p,n);
        case 2: return sys_write(fd,p,n);
        case 3: return sys_write_nocancel(fd,p,n);
        default: return __write_for_xtrace(fd,p,n);
    }
}
static void injected_arguments(void) {
    injected = 1;
    for (int i=0;i<5;i++) {
        expected_nr = i<2 ? SYS_read : SYS_write;
        memset(expected,0,sizeof(expected)); expected[0]=-42;
        expected[1]=(long)(uintptr_t)UINT64_C(0x123456789abcdef0); expected[2]=123;
        const long results[] = {0,17,-4,-11,-9,-14};
        const long want[] = {0,17,-4,-35,-9,-14};
        for(unsigned j=0;j<sizeof(results)/sizeof(results[0]);j++) {
            raw_result=results[j]; int before=syscall_calls, cancel_before=cancel_calls;
            assert(invoke(i,-42,(void *)(uintptr_t)expected[1],123)==want[j]);
            assert(syscall_calls==before+1 && ioctl_calls==0 && stat_calls==0);
            assert(cancel_calls==cancel_before+(i==0 || i==2));
        }
        /* Only zero from the cancellation probe means cancellation. */
        for(int state=-1;state<=1;state++) {
            cancel_result=state; raw_result=3;
            int before=syscall_calls;
            long want_result=state==0 && (i==0 || i==2) ? -4 : 3;
            assert(invoke(i,-42,(void *)(uintptr_t)expected[1],123)==want_result);
            assert(syscall_calls==before+(want_result==3));
        }
        cancel_result=0; int before=syscall_calls;
        if(i==0 || i==2) {
            assert(invoke(i,INT_MIN,(void *)UINTPTR_MAX,SIZE_MAX)==-4);
            assert(syscall_calls==before);
        }
        cancel_result=1;
    }
    /* EIO becomes EOF only for reads whose real terminal query succeeds. */
    expected_nr=SYS_read; expected[0]=-42; expected[2]=123; raw_result=-5;
    ioctl_mode=1; assert(sys_read_nocancel(-42,(void *)(uintptr_t)expected[1],123)==0);
    ioctl_mode=2; assert(sys_read_nocancel(-42,(void *)(uintptr_t)expected[1],123)==-5);
    for(int mode=1;mode<=5;mode++) {
        stat_mode=mode; int before=stat_calls;
        assert(sys_read_nocancel(-42,(void *)(uintptr_t)expected[1],123)==(mode==1 ? 0 : -5));
        assert(stat_calls==before+1);
    }
    stat_mode=0;
    expected_nr=SYS_write; int before=ioctl_calls;
    assert(sys_write_nocancel(-42,(void *)(uintptr_t)expected[1],123)==-5);
    assert(ioctl_calls==before); ioctl_mode=0; injected=0;
}
static void *eio_worker(void *index) {
    worker_index=(int)(intptr_t)index; injected=1; expected_nr=SYS_read;
    expected[0]=10+worker_index; expected[1]=1; expected[2]=1; raw_result=-5;
    assert(sys_read_nocancel((int)expected[0],(void *)1,1)==0);
    return NULL;
}
static void concurrent_eio(void) {
    concurrent_ioctl=1; assert(pthread_barrier_init(&ioctl_barrier,NULL,2)==0);
    pthread_t threads[2];
    for(int i=0;i<2;i++) assert(pthread_create(&threads[i],NULL,eio_worker,(void *)(intptr_t)i)==0);
    for(int i=0;i<2;i++) assert(pthread_join(threads[i],NULL)==0);
    assert(pthread_barrier_destroy(&ioctl_barrier)==0); concurrent_ioctl=0;
}
static void oversized(int rw, int ro, int wo, int path, int neither) {
    reject_io=1;
    const size_t lengths[]={(size_t)INT_MAX+1, UINT64_C(0x100000001), SIZE_MAX};
    for(unsigned j=0;j<sizeof(lengths)/sizeof(lengths[0]);j++) {
        for(int i=0;i<5;i++) {
            int writing=i>=2;
            off_t offset=lseek(rw,0,SEEK_CUR); struct stat before,after;
            assert(fstat(rw,&before)==0);
            assert(invoke(i,rw,(void *)1,lengths[j])==-22);
            assert(invoke(i,writing?ro:wo,(void *)1,lengths[j])==-9);
            assert(invoke(i,-1,(void *)1,lengths[j])==-9);
            assert(invoke(i,path,(void *)1,lengths[j])==-9);
            assert(invoke(i,neither,(void *)1,lengths[j])==-9);
            assert(lseek(rw,0,SEEK_CUR)==offset && fstat(rw,&after)==0 && before.st_size==after.st_size);
        }
    }
    reject_io=0;
}
static volatile sig_atomic_t signals;
static void interrupted(int sig) { assert(sig==SIGUSR1); signals++; }
struct interrupt_context { pthread_t target; int writer; };
static void *interrupt_thread(void *arg) {
    struct interrupt_context *ctx=arg;
    usleep(30000); assert(pthread_kill(ctx->target,SIGUSR1)==0);
    /* A broken implicit retry must complete and fail its result assertion, not hang. */
    usleep(30000); assert(write(ctx->writer,"x",1)==1); return NULL;
}
static void slave_background_eio(int slave) {
    pid_t owner=fork(); assert(owner>=0);
    if(owner==0) {
        assert(setsid()>=0 && ioctl(slave,TIOCSCTTY,0)==0);
        pid_t background=fork(); assert(background>=0);
        if(background==0) {
            assert(setpgid(0,0)==0 && signal(SIGTTIN,SIG_IGN)!=SIG_ERR);
            char byte; errno=0;
            assert(read(slave,&byte,1)==-1 && errno==EIO);
            assert(sys_read_nocancel(slave,&byte,1)==-5);
            _exit(0);
        }
        int status; assert(waitpid(background,&status,0)==background);
        assert(WIFEXITED(status) && WEXITSTATUS(status)==0); _exit(0);
    }
    int status; assert(waitpid(owner,&status,0)==owner);
    assert(WIFEXITED(status) && WEXITSTATUS(status)==0);
}
static void real_io(void) {
    char directory[]="/tmp/dearust-stream-XXXXXX"; assert(mkdtemp(directory));
    char name[128]; assert(snprintf(name,sizeof(name),"%s/data",directory)>0);
    int rw=open(name,O_CREAT|O_EXCL|O_RDWR,0600), ro=open(name,O_RDONLY), wo=open(name,O_WRONLY);
    int path=open(name,O_PATH), neither=open(name,3);
    assert(rw>=0 && ro>=0 && wo>=0 && path>=0 && neither>=0);
    assert(sys_write(rw,"abcdef",6)==6 && lseek(rw,0,SEEK_SET)==0);
    char data[16]={0}; assert(sys_read(rw,data,2)==2 && memcmp(data,"ab",2)==0);
    assert(sys_read_nocancel(rw,data,16)==4 && memcmp(data,"cdef",4)==0);
    assert(sys_read_nocancel(rw,(void *)1,1)==0); /* EOF wins over unused pointer. */
    assert(lseek(rw,0,SEEK_SET)==0 && sys_read_nocancel(rw,(void *)1,1)==-14);
    assert(sys_write_nocancel(rw,(void *)1,1)==-14);
    assert(sys_read(-1,NULL,0)==-9 && sys_write(-1,NULL,0)==-9);
    assert(sys_read(rw,NULL,0)==0 && __write_for_xtrace(rw,NULL,0)==0);
    oversized(rw,ro,wo,path,neither);
    int fds[2]; assert(pipe2(fds,O_NONBLOCK)==0);
    assert(sys_read_nocancel(fds[0],data,sizeof(data))==-35);
    assert(sys_write_nocancel(fds[1],"short",5)==5 && sys_read_nocancel(fds[0],data,16)==5);
    assert(memcmp(data,"short",5)==0);
    /* A nonblocking >PIPE_BUF write must preserve the short successful count. */
    int capacity=fcntl(fds[1],F_GETPIPE_SZ); assert(capacity>=4096);
    char *large=malloc((size_t)capacity*2); assert(large); memset(large,'q',(size_t)capacity*2);
    long count=sys_write_nocancel(fds[1],large,(size_t)capacity*2);
    assert(count>0 && count<=capacity);
    assert(sys_write_nocancel(fds[1],large,(size_t)capacity*2)==-35);
    free(large); assert(close(fds[0])==0 && close(fds[1])==0);
    assert(pipe(fds)==0);
    struct sigaction action={0}, previous; action.sa_handler=interrupted;
    assert(sigemptyset(&action.sa_mask)==0 && sigaction(SIGUSR1,&action,&previous)==0);
    struct interrupt_context ctx={pthread_self(),fds[1]}; pthread_t thread;
    assert(pthread_create(&thread,NULL,interrupt_thread,&ctx)==0);
    assert(sys_read_nocancel(fds[0],data,1)==-4);
    assert(pthread_join(thread,NULL)==0 && signals==1 && sys_read(fds[0],data,1)==1);
    assert(close(fds[1])==0 && sys_read(fds[0],data,1)==0 && close(fds[0])==0);
    assert(sigaction(SIGUSR1,&previous,NULL)==0);
    int master=posix_openpt(O_RDWR|O_NOCTTY); assert(master>=0);
    assert(grantpt(master)==0 && unlockpt(master)==0);
    char slave_name[128]; assert(ptsname_r(master,slave_name,sizeof(slave_name))==0);
    int slave=open(slave_name,O_RDWR|O_NOCTTY); assert(slave>=0);
    slave_background_eio(slave);
    /* Even terminal EIO must stay EIO on a slave: only master hangup maps EOF. */
    injected=1; expected_nr=SYS_read; memset(expected,0,sizeof(expected));
    expected[0]=slave; expected[1]=(long)(uintptr_t)data; expected[2]=1; raw_result=-5;
    assert(sys_read_nocancel(slave,data,1)==-5); injected=0;
    assert(close(slave)==0); errno=0;
    assert(read(master,data,1)==-1 && errno==EIO);
    assert(sys_read_nocancel(master,data,1)==0 && close(master)==0);
    /* The same injected EIO on an ordinary file must remain EIO. */
    injected=1; expected_nr=SYS_read; memset(expected,0,sizeof(expected));
    expected[0]=rw; expected[1]=(long)(uintptr_t)data; expected[2]=1; raw_result=-5;
    assert(sys_read_nocancel(rw,data,1)==-5); injected=0;
    assert(close(neither)==0 && close(path)==0 && close(ro)==0 && close(wo)==0 && close(rw)==0);
    assert(unlink(name)==0 && rmdir(directory)==0);
}
int main(void) {
    injected_arguments(); concurrent_eio(); real_io();
    puts("stream: five exports, cancellation, widths, no retry, fd precedence, real I/O/PTY and concurrent local TIOCGPTN");
}
