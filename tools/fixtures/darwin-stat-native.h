/* Native x86_64 adapters for the actual XNU stat120/stat64 layouts. Linux
 * headers remain host headers; independent Mach-O SDK assertions validate ABI. */
#ifndef DEARUST_DARWIN_STAT_NATIVE_H
#define DEARUST_DARWIN_STAT_NATIVE_H
#include <stddef.h>
struct native_time { int64_t sec, nsec; };
struct native_stat120 {
    int32_t dev; uint32_t ino; uint16_t mode, nlink; uint32_t uid, gid; int32_t rdev;
    struct native_time atime, mtime, ctime;
    int64_t size, blocks; int32_t blksize; uint32_t flags, gen; int32_t spare;
    int64_t qspare[2];
};
struct native_stat144 {
    int32_t dev; uint16_t mode, nlink; uint64_t ino; uint32_t uid, gid; int32_t rdev;
    struct native_time atime, mtime, ctime, birthtime;
    int64_t size, blocks; int32_t blksize; uint32_t flags, gen; int32_t spare;
    int64_t qspare[2];
};
_Static_assert(sizeof(struct native_stat120)==120 && offsetof(struct native_stat120,size)==72 && offsetof(struct native_stat120,atime)==24,"Darwin stat120");
_Static_assert(sizeof(struct native_stat144)==144 && offsetof(struct native_stat144,size)==96 && offsetof(struct native_stat144,atime)==32,"Darwin stat144");
static inline struct stat native_view(const void *bytes,int wide){
    struct stat out={0};
#define VIEW_FIELDS(v) do {out.st_dev=(uint32_t)(v).dev;out.st_ino=(v).ino;out.st_mode=(v).mode;out.st_nlink=(v).nlink;out.st_uid=(v).uid;out.st_gid=(v).gid;out.st_rdev=(uint32_t)(v).rdev;out.st_size=(v).size;out.st_blocks=(v).blocks;out.st_blksize=(v).blksize;out.st_atim.tv_sec=(v).atime.sec;out.st_atim.tv_nsec=(v).atime.nsec;out.st_mtim.tv_sec=(v).mtime.sec;out.st_mtim.tv_nsec=(v).mtime.nsec;out.st_ctim.tv_sec=(v).ctime.sec;out.st_ctim.tv_nsec=(v).ctime.nsec;} while(0)
    if(wide){struct native_stat144 v;memcpy(&v,bytes,sizeof(v));VIEW_FIELDS(v);}
    else {struct native_stat120 v;memcpy(&v,bytes,sizeof(v));VIEW_FIELDS(v);}
#undef VIEW_FIELDS
    return out;
}
/* Original entries used in the historical native benchmark call these C
 * adapters; Rust calls __getuidgid. Both expose the same process credentials. */
static inline void native_convert(const struct stat *in,void *out,int wide){
#define CONVERT_FIELDS(v) do{(v).dev=in->st_dev;(v).ino=in->st_ino;(v).mode=in->st_mode;(v).nlink=in->st_nlink;(v).uid=getuid();(v).gid=getgid();(v).rdev=in->st_rdev;(v).size=in->st_size;(v).blocks=in->st_blocks;(v).blksize=in->st_blksize;(v).atime=(struct native_time){in->st_atim.tv_sec,in->st_atim.tv_nsec};(v).mtime=(struct native_time){in->st_mtim.tv_sec,in->st_mtim.tv_nsec};(v).ctime=(struct native_time){in->st_ctim.tv_sec,in->st_ctim.tv_nsec};}while(0)
    if(wide){struct native_stat144 v={0};CONVERT_FIELDS(v);memcpy(out,&v,sizeof(v));}
    else {struct native_stat120 v={0};CONVERT_FIELDS(v);memcpy(out,&v,sizeof(v));}
#undef CONVERT_FIELDS
}
#endif
