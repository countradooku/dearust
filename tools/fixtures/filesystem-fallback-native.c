/* Native-only deterministic pressure/policy adapters; never production code. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

struct expanded { char path[4096]; unsigned flags; int dfd; };
struct filesystem {
    uint32_t bsize; int32_t iosize;
    uint64_t blocks,bfree,bavail,files,ffree,fsid;
    uint32_t owner,type,flags,subtype;
    char typename[16],point[1024],source[1024];
    uint32_t extended,reserved[7];
};
struct guarded_output { uint64_t before; struct filesystem fs; uint64_t after; };
_Static_assert(sizeof(struct filesystem)==2168,"Darwin x64 filesystem layout");
extern long sys_statfs(const char*,void*),sys_statfs64(const char*,void*);
extern long sys_fstatfs(int,void*),sys_fstatfs64(int,void*);

static const char query[]="/query/source";
static const uint64_t identity=UINT64_C(0x123456789abcdef0);
enum scenario {
    path_open_full, fdinfo_full, mountinfo_full, late_read_full, pin_full,
    server_full_pinned, path_denied, fd_denied, anonymous_fd,
    path_id_mismatch, fd_id_mismatch, path_type_mismatch, pin_einval, fd_denied_einval, anonymous_fd_einval, statx_valid, statx_missing_mask, statx_denied,
    statmount_valid, statmount_malformed, statmount_overflow, statmount_no_write
};
static enum scenario scenario;
static unsigned rpc_calls,probe137,probe138,statx_calls,closed[32];
static int rpc_fd;
static char long_record[1024];
static size_t long_record_length;

int dearust_copy_signals_ready(void){return 0;}
int dearust_get_perthread_wd(void){return -100;}
int vchroot_expand(struct expanded *value){
    assert(value->flags==1 && value->dfd==-100);
    assert(strcmp(value->path,query)==0);return 0;
}
int vchroot_unexpand(void *point){assert(strcmp(point,"/mount")==0);return 0;}

static void stat_result(uint64_t *words){
    memset(words,0,120);words[0]=0xef53;words[1]=4096;
    words[2]=9000;words[3]=8000;words[4]=7000;words[5]=6000;words[6]=5000;
    words[7]=identity;words[9]=1024;
}
static long copy_chunk(long destination,long capacity,const char *bytes,size_t length,long offset){
    assert(capacity>=512 && offset>=0);
    if((uint64_t)offset>=length)return 0;
    size_t count=length-(size_t)offset;if(count>512)count=512; /* Short reads exercise chunk boundaries at any buffer size. */
    memcpy((void*)destination,bytes+offset,count);return (long)count;
}
long linux_syscall(long a,long b,long c,long d,long e,long f,int nr){
    switch(nr){
    case 3:
        /* Fake private descriptors must never reach a real close, and the
         * borrowed source77 must never be closed by the implementation. */
        assert(a==10 || a==20 || a==21);assert(++closed[a]==1);return 0;
    case 72:
        assert(a==77 && b==1030 && c==0);
        if(scenario==pin_einval || scenario==fd_denied_einval || scenario==anonymous_fd_einval)return -EINVAL;
        if(scenario==pin_full || scenario==fd_denied || scenario==anonymous_fd || scenario==fd_id_mismatch)return -EMFILE;
        return 10;
    case 457: {
        const uint64_t *request=(const void*)a;
        assert(request[0]==24 && request[1]==UINT64_C(0x1000000000001) && request[2]==0x330);
        assert(c==4096 && d==0);
        uint32_t initial_size;memcpy(&initial_size,(const void*)b,4);assert(initial_size==0);
        if(scenario==statmount_no_write)return 0; /* seccomp-style synthetic success. */
        if(scenario==statmount_overflow)return -EOVERFLOW;
        assert(scenario==statmount_valid || scenario==statmount_malformed);
        unsigned char *bytes=(void*)b;memset(bytes,0,4096);
        uint64_t mask=scenario==statmount_valid?0x330:0x320;memcpy(bytes+8,&mask,8);
        const char *values[]={"/mount","fuse","/dev/fake","sshfs"};
        const unsigned offsets[]={108,36,124,120};
        uint32_t cursor=513;
        for(unsigned index=0;index<4;index++){
            uint32_t relative=cursor-512;memcpy(bytes+offsets[index],&relative,4);
            size_t length=strlen(values[index])+1;memcpy(bytes+cursor,values[index],length);cursor+=(uint32_t)length;
        }
        memcpy(bytes,&cursor,4);return 0;
    }
    case 332: {
        ++statx_calls;
        assert(a==10 && ((const char*)b)[0]==0 && c==0x5000 && (d==0x1000 || d==0x4000));
        if(d==0x4000){
            if(scenario==statmount_valid || scenario==statmount_malformed || scenario==statmount_overflow || scenario==statmount_no_write){
                uint64_t *value=(void*)e;memset(value,0,256);value[0]=0x4000;
                value[18]=UINT64_C(0x1000000000001);return 0;
            }
            return -ENOSYS;
        }
        if(scenario==statx_denied)return -EPERM;
        if(scenario==statx_valid || scenario==statx_missing_mask || scenario==statmount_malformed || scenario==statmount_overflow || scenario==statmount_no_write){
            uint64_t *value=(void*)e;memset(value,0,256);
            value[0]=scenario==statx_missing_mask?0:0x1000;
            value[18]=scenario==statx_missing_mask?777:42;
            return 0;
        }
        return -ENOSYS; /* Keep deterministic procfs pressure stages covered. */
    }
    case 137:
        ++probe137;assert(strcmp((const char*)a,query)==0);
        if(scenario==path_denied)return -EACCES;
        stat_result((uint64_t*)b);return 0;
    case 138:
        ++probe138;assert(a==10 || a==77);
        if(scenario==fd_denied || scenario==fd_denied_einval)return -EACCES;
        stat_result((uint64_t*)b);
        if(scenario==anonymous_fd || scenario==anonymous_fd_einval)((uint64_t*)b)[0]=0x50495045;
        return 0;
    case 257: {
        const char *path=(const char*)b;assert(a==-100);
        if(strcmp(path,query)==0){
            assert(c==0x280000);
            if(scenario==path_open_full || scenario==path_denied || scenario==path_id_mismatch || scenario==path_type_mismatch)return -EMFILE;
            return 10;
        }
        assert(c==0x80000);
        if(strcmp(path,"/proc/thread-self/fdinfo/10")==0){
            if(scenario==fdinfo_full || scenario==server_full_pinned)return -EMFILE;
            return 20;
        }
        assert(strcmp(path,"/proc/thread-self/mountinfo")==0);
        if(scenario==mountinfo_full)return -EMFILE;
        return 21;
    }
    case 17:
        if(a==20)return copy_chunk(b,c,"mnt_id: 42\n",11,d);
        assert(a==21);
        if(scenario==late_read_full){
            if(d>=512)return -EMFILE;
            return copy_chunk(b,c,long_record,long_record_length,d);
        }
        assert(scenario==statx_valid || scenario==statx_missing_mask || scenario==statx_denied || scenario==statmount_malformed || scenario==statmount_overflow || scenario==statmount_no_write);
        {
            const char record[]="42 1 0:1 / /mount rw - ext4 /dev/fake rw\n";
            return copy_chunk(b,c,record,sizeof(record)-1,d);
        }
    case 14: case 39: case 310: case 311: {
        /* Exercise the real bounded kernel guest copies/signal-mask restore. */
        long result=syscall(nr,a,b,c,d,e,f);return result<0?-errno:result;
    }
    default:fprintf(stderr,"unexpected Linux syscall%d\n",nr);abort();
    }
}

long dearust_filesystem_server_snapshot(int fd,int directory,const unsigned char *path,
                                      unsigned length,void *output,void *point){
    ++rpc_calls;rpc_fd=fd;
    if(fd<0){
        assert(directory==-100 && length==sizeof(query));
        assert(path && memcmp(path,query,sizeof(query))==0 && probe137==1);
    }else{
        assert(directory==-1 && !path && length==0 && probe138>=1);
        assert(fd==10 || fd==77);
        if(fd==10)assert(closed[10]==0); /* Still pinned across the RPC. */
    }
    if(scenario==server_full_pinned)return -24;
    struct filesystem *fs=output;memset(fs,0,sizeof(*fs));
    fs->bsize=1024;fs->iosize=4096;fs->blocks=9000;fs->bfree=8000;fs->bavail=7000;
    fs->files=6000;fs->ffree=5000;fs->fsid=identity;fs->type=0xef53;
    strcpy(fs->typename,"ext4");strcpy(fs->source,"/dev/fake");
    if(scenario==path_id_mismatch || scenario==fd_id_mismatch)fs->fsid++;
    if(scenario==path_type_mismatch)fs->type++;
    memset(point,0,4096);strcpy(point,"/mount");return 0;
}

static void reset(enum scenario selected){
    scenario=selected;rpc_calls=probe137=probe138=statx_calls=0;rpc_fd=-99;memset(closed,0,sizeof(closed));
}
static void run(enum scenario selected,int descriptor,int wide,long expected,unsigned requests,int expected_fd){
    reset(selected);
    struct guarded_output output;memset(&output,0xa5,sizeof(output));
    struct guarded_output before=output;
    long result=descriptor
        ?(wide?sys_fstatfs64(77,&output.fs):sys_fstatfs(77,&output.fs))
        :(wide?sys_statfs64(query,&output.fs):sys_statfs(query,&output.fs));
    assert(result==expected && rpc_calls==requests);
    if(requests)assert(rpc_fd==expected_fd);
    assert(output.before==before.before && output.after==before.after);
    if(expected){assert(memcmp(&output,&before,sizeof(output))==0);}
    else {
        assert(output.fs.bsize==1024 && output.fs.iosize==4096 && output.fs.fsid==identity);
        assert(strcmp(output.fs.point,"/mount")==0 && output.fs.point[1023]==0);
    }
    if(selected==late_read_full)assert(probe137==0 && rpc_calls==1 && rpc_fd==10 && closed[10]==1 && closed[20]==1 && closed[21]==1);
    if(selected==server_full_pinned)assert(rpc_calls==1 && rpc_fd==10 && closed[10]==1);
    if(selected==fd_denied || selected==anonymous_fd || selected==fd_denied_einval || selected==anonymous_fd_einval)assert(probe138==1 && rpc_calls==0);
    if(selected==path_denied)assert(probe137==1 && rpc_calls==0);
    if(selected==statx_valid || selected==statx_missing_mask || selected==statx_denied){
        assert(statx_calls==2 && closed[10]==1 && closed[21]==1);
        assert(closed[20]==(selected==statx_valid?0:1));
    }
    if(selected==statmount_valid){
        assert(statx_calls==1 && closed[10]==1 && closed[20]==0 && closed[21]==0);
        assert(strcmp(output.fs.typename,"fuse.sshfs")==0);
    }
    if(selected==statmount_malformed || selected==statmount_overflow || selected==statmount_no_write){
        assert(statx_calls==2 && closed[10]==1 && closed[20]==0 && closed[21]==1);
    }
    assert(closed[10]<=1 && closed[20]<=1 && closed[21]<=1);
}
int main(void){
    alarm(30);
    int prefix=snprintf(long_record,sizeof(long_record),"42 1 0:1 / /");assert(prefix>0);
    memset(long_record+prefix,'x',700);
    const char suffix[]=" rw - ext4 /dev/fake rw\n";
    memcpy(long_record+prefix+700,suffix,sizeof(suffix)-1);
    long_record_length=(size_t)prefix+700+sizeof(suffix)-1;
    for(int wide=0;wide<2;wide++){
        run(path_open_full,0,wide,0,1,-1);  /* No spare descriptor. */
        run(fdinfo_full,0,wide,0,1,10);     /* Pin succeeds; first proc open fails. */
        run(mountinfo_full,0,wide,0,1,10);  /* fdinfo succeeds; mountinfo open fails. */
        run(late_read_full,0,wide,0,1,10); /* Use pinned FD, never the overwritten pathname. */
        run(pin_full,1,wide,0,1,77);
        run(pin_einval,1,wide,0,1,77); /* min0 >= hardRLIMIT0 yields EINVAL. */
        run(fd_denied_einval,1,wide,-13,0,-99);
        run(anonymous_fd_einval,1,wide,-22,0,-99);
        run(fdinfo_full,1,wide,0,1,10);
        run(server_full_pinned,1,wide,-24,1,10); /* Never retry with unpinned source. */
        run(path_denied,0,wide,-13,0,-99);
        run(fd_denied,1,wide,-13,0,-99);
        run(anonymous_fd,1,wide,-22,0,-99);
        run(path_id_mismatch,0,wide,-70,1,-1);
        run(fd_id_mismatch,1,wide,-70,1,77);
        run(path_type_mismatch,0,wide,-70,1,-1);
        for(int descriptor=0;descriptor<2;descriptor++){
            run(statx_valid,descriptor,wide,0,0,-99);
            run(statx_missing_mask,descriptor,wide,0,0,-99);
            run(statx_denied,descriptor,wide,0,0,-99);
            run(statmount_valid,descriptor,wide,0,0,-99);
            run(statmount_malformed,descriptor,wide,0,0,-99);
            run(statmount_overflow,descriptor,wide,0,0,-99);
            run(statmount_no_write,descriptor,wide,0,0,-99);
        }
    }
    puts("filesystem fallback: pressure stages, pinned ownership, single RPC, late-path preservation, client policy, anonymous fd and identity checks pass");
    return 0;
}
