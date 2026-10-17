/* Linux maps escapes newlines ambiguously; preserve the actual vnode pathname. */
#include <assert.h>
#include <fcntl.h>
#include <libproc.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
static void check(const char *directory, const char *name) {
    char path[1024]; assert(snprintf(path,sizeof(path),"%s/%s",directory,name)>0);
    int fd=open(path,O_CREAT|O_EXCL|O_RDWR,0600); assert(fd>=0);
    assert(ftruncate(fd,4096)==0);
    struct stat sb; assert(fstat(fd,&sb)==0);
    void *mapping=mmap(NULL,4096,PROT_READ,MAP_PRIVATE,fd,0); assert(mapping!=MAP_FAILED);
    const unsigned flavors[]={PROC_PIDREGIONPATHINFO,22};
    for(unsigned i=0;i<2;++i) {
    struct proc_regionwithpathinfo region;
    assert(proc_pidinfo(getpid(),flavors[i],(uint64_t)mapping,&region,sizeof(region))==sizeof(region));
    assert(region.prp_prinfo.pri_address==(uint64_t)mapping);
    assert(region.prp_vip.vip_vi.vi_stat.vst_ino==sb.st_ino);
    assert(region.prp_vip.vip_vi.vi_stat.vst_dev==(uint32_t)sb.st_dev);
    if(strcmp(region.prp_vip.vip_path,path)!=0) {
        fprintf(stderr,"mapped path mismatch: actual <%s>, expected <%s>\n",region.prp_vip.vip_path,path);
        assert(strcmp(region.prp_vip.vip_path,path)==0);
    }
    }
    assert(close(fd)==0&&munmap(mapping,4096)==0&&unlink(path)==0);
}
int main(void) {
    alarm(30);
    char directory[]="/private/tmp/dearust-map-path-XXXXXX"; assert(mkdtemp(directory)!=NULL);
    check(directory,"actual\nnewline");
    check(directory,"literal\\012sequence");
    check(directory,"ordinary\\backslash");
    assert(rmdir(directory)==0);
    puts("Dearust mapped pathname regression checks passed");
    return 0;
}
