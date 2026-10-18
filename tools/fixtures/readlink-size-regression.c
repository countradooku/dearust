/* Independent reproduction of Darling's size_t-to-int readlinkat truncation. */
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
int main(void){
    alarm(30);char path[]="/private/tmp/dearust-linksize-XXXXXX";assert(mkdtemp(path));int fd=open(path,O_RDONLY);assert(fd>=0);
    assert(symlinkat("target",fd,"soft")==0);unsigned char output[16];memset(output,0xa5,sizeof(output));
    errno=0;long value=syscall(SYS_readlinkat,fd,"soft",output,UINT64_C(0x100000001));int error=errno;
    fprintf(stderr,"READLINK_SIZE count=4294967297 value=%ld errno=%d first_byte=%u\n",value,error,(unsigned)output[0]);
    assert(value==-1 && error==EINVAL && output[0]==0xa5);
    assert(unlinkat(fd,"soft",0)==0 && close(fd)==0 && rmdir(path)==0);
    puts("Dearust readlink size-width regression checks passed");return 0;
}
