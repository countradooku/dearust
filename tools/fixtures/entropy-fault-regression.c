/* Non-aborting actual-guest evidence of all three original entropy defects. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>
int main(void){
    alarm(30);unsigned char bytes[258];memset(bytes,0xa5,sizeof(bytes));
    errno=0;long result=syscall(SYS_getentropy,NULL,1);int error=errno;
    printf("null result=%ld errno=%d\n",result,error);
    errno=0;result=syscall(SYS_getentropy,bytes,257);error=errno;
    printf("oversize result=%ld errno=%d untouched=%d\n",result,error,bytes[0]==0xa5 && bytes[256]==0xa5);
    long page=sysconf(_SC_PAGESIZE);assert(page>256);
    unsigned char *mapping=mmap(NULL,(size_t)page*2,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    assert(mapping!=MAP_FAILED);memset(mapping,0xa5,(size_t)page);
    assert(mprotect(mapping+page,(size_t)page,PROT_NONE)==0);
    errno=0;result=syscall(SYS_getentropy,mapping+page-16,32);error=errno;
    printf("partial result=%ld errno=%d guard=%u\n",result,error,mapping[page-17]);
    assert(munmap(mapping,(size_t)page*2)==0);return 0;
}
