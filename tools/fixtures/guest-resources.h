/* Read Linux's own process snapshot, after timing. No host PID guessing and no
 * Darwin rusage-unit assumption. Kernel text reports these counters in kB. */
#ifndef DEARUST_GUEST_RESOURCES_H
#define DEARUST_GUEST_RESOURCES_H
#include <assert.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
struct linux_memory_sample {unsigned long rss,pss,private_dirty;};
static unsigned long resource_field(const char *text,const char *name){
    const char *field=strstr(text,name);assert(field);return strtoul(field+strlen(name),NULL,10);
}
static struct linux_memory_sample resource_memory(void){
    char text[2048]={0};int fd=open("/proc/self/smaps_rollup",O_RDONLY);assert(fd>=0);
    ssize_t length=read(fd,text,sizeof(text)-1);assert(length>0 && close(fd)==0);text[length]=0;
    struct linux_memory_sample sample={resource_field(text,"\nRss:"),resource_field(text,"\nPss:"),resource_field(text,"\nPrivate_Dirty:")};
    return sample;
}
#endif
