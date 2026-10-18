/* SPDX-License-Identifier: GPL-3.0-or-later
 * Owned Linux ptrace-register snapshots assembled into minimal real-regset ELF cores.
 * Does not change core_pattern, attach to unrelated processes, or run in Darling. */
#define _GNU_SOURCE
#include <elf.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/ptrace.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/uio.h>
#include <sys/wait.h>
#include <unistd.h>
#ifndef NT_X86_XSTATE
#define NT_X86_XSTATE 0x202
#endif
#ifndef NT_PRXFPREG
#define NT_PRXFPREG 0x46e62b7f
#endif
static pid_t owned_child=-1;
static void cleanup(void) {
    if(owned_child>0) {
        kill(owned_child,SIGKILL);
        for(;;) {
            pid_t result=waitpid(-1,NULL,__WALL);
            if(result>0)continue;
            if(result<0 && errno==EINTR)continue;
            break;
        }
        owned_child=-1;
    }
}
static void failure(const char *what) {
    int saved=errno;
    cleanup();
    fprintf(stderr,"core fixture: %s: %s\n",what,strerror(saved));
    exit(1);
}
#define REQUIRE(test) do{if(!(test))failure(#test);}while(0)
struct context {
    _Atomic unsigned ready;
    pid_t tid;
    unsigned index;
    int mode;
    uint16_t control;
    uint32_t mxcsr;
    uint64_t mask;
    unsigned char extended[16];
    unsigned char vectors[32][64];
};
static void *register_loop(void *opaque) {
    struct context *ctx=opaque;
    ctx->tid=(pid_t)syscall(SYS_gettid);
    if(ctx->mode==2) {
        __asm__ volatile(
        "fninit\n\tfldcw %[cw]\n\tfldt %[fp]\n\tldmxcsr %[mx]\n\t"
        "vmovdqu64 0(%[v]), %%zmm0\n\t"
        "vmovdqu64 64(%[v]), %%zmm1\n\t"
        "vmovdqu64 128(%[v]), %%zmm2\n\t"
        "vmovdqu64 192(%[v]), %%zmm3\n\t"
        "vmovdqu64 256(%[v]), %%zmm4\n\t"
        "vmovdqu64 320(%[v]), %%zmm5\n\t"
        "vmovdqu64 384(%[v]), %%zmm6\n\t"
        "vmovdqu64 448(%[v]), %%zmm7\n\t"
        "vmovdqu64 512(%[v]), %%zmm8\n\t"
        "vmovdqu64 576(%[v]), %%zmm9\n\t"
        "vmovdqu64 640(%[v]), %%zmm10\n\t"
        "vmovdqu64 704(%[v]), %%zmm11\n\t"
        "vmovdqu64 768(%[v]), %%zmm12\n\t"
        "vmovdqu64 832(%[v]), %%zmm13\n\t"
        "vmovdqu64 896(%[v]), %%zmm14\n\t"
        "vmovdqu64 960(%[v]), %%zmm15\n\t"
        "vmovdqu64 1024(%[v]), %%zmm16\n\t"
        "vmovdqu64 1088(%[v]), %%zmm17\n\t"
        "vmovdqu64 1152(%[v]), %%zmm18\n\t"
        "vmovdqu64 1216(%[v]), %%zmm19\n\t"
        "vmovdqu64 1280(%[v]), %%zmm20\n\t"
        "vmovdqu64 1344(%[v]), %%zmm21\n\t"
        "vmovdqu64 1408(%[v]), %%zmm22\n\t"
        "vmovdqu64 1472(%[v]), %%zmm23\n\t"
        "vmovdqu64 1536(%[v]), %%zmm24\n\t"
        "vmovdqu64 1600(%[v]), %%zmm25\n\t"
        "vmovdqu64 1664(%[v]), %%zmm26\n\t"
        "vmovdqu64 1728(%[v]), %%zmm27\n\t"
        "vmovdqu64 1792(%[v]), %%zmm28\n\t"
        "vmovdqu64 1856(%[v]), %%zmm29\n\t"
        "vmovdqu64 1920(%[v]), %%zmm30\n\t"
        "vmovdqu64 1984(%[v]), %%zmm31\n\t"
        "kmovq %[mask], %%k1\n\tkmovq %[mask], %%k7\n\t"
        "movl $1, (%[ready])\n\t1: pause\n\tjmp 1b"
        : : [v]"r"(ctx->vectors),[cw]"m"(ctx->control),[fp]"m"(ctx->extended),[mx]"m"(ctx->mxcsr),[ready]"r"(&ctx->ready),[mask]"m"(ctx->mask)
        : "memory","cc","zmm0","zmm1","zmm2","zmm3","zmm4","zmm5","zmm6","zmm7","zmm8","zmm9","zmm10","zmm11","zmm12","zmm13","zmm14","zmm15","zmm16","zmm17","zmm18","zmm19","zmm20","zmm21","zmm22","zmm23","zmm24","zmm25","zmm26","zmm27","zmm28","zmm29","zmm30","zmm31","k1","k7");
    }
    else if(ctx->mode==1) {
        __asm__ volatile(
        "fninit\n\tfldcw %[cw]\n\tfldt %[fp]\n\tldmxcsr %[mx]\n\t"
        "vmovdqu 0(%[v]), %%ymm0\n\t"
        "vmovdqu 64(%[v]), %%ymm1\n\t"
        "vmovdqu 128(%[v]), %%ymm2\n\t"
        "vmovdqu 192(%[v]), %%ymm3\n\t"
        "vmovdqu 256(%[v]), %%ymm4\n\t"
        "vmovdqu 320(%[v]), %%ymm5\n\t"
        "vmovdqu 384(%[v]), %%ymm6\n\t"
        "vmovdqu 448(%[v]), %%ymm7\n\t"
        "vmovdqu 512(%[v]), %%ymm8\n\t"
        "vmovdqu 576(%[v]), %%ymm9\n\t"
        "vmovdqu 640(%[v]), %%ymm10\n\t"
        "vmovdqu 704(%[v]), %%ymm11\n\t"
        "vmovdqu 768(%[v]), %%ymm12\n\t"
        "vmovdqu 832(%[v]), %%ymm13\n\t"
        "vmovdqu 896(%[v]), %%ymm14\n\t"
        "vmovdqu 960(%[v]), %%ymm15\n\t"
        "movl $1, (%[ready])\n\t1: pause\n\tjmp 1b"
        : : [v]"r"(ctx->vectors),[cw]"m"(ctx->control),[fp]"m"(ctx->extended),[mx]"m"(ctx->mxcsr),[ready]"r"(&ctx->ready)
        : "memory","cc","ymm0","ymm1","ymm2","ymm3","ymm4","ymm5","ymm6","ymm7","ymm8","ymm9","ymm10","ymm11","ymm12","ymm13","ymm14","ymm15");
    }
    else {
        __asm__ volatile(
        "fninit\n\tfldcw %[cw]\n\tfldt %[fp]\n\tldmxcsr %[mx]\n\t"
        "movdqu 0(%[v]), %%xmm0\n\t"
        "movdqu 64(%[v]), %%xmm1\n\t"
        "movdqu 128(%[v]), %%xmm2\n\t"
        "movdqu 192(%[v]), %%xmm3\n\t"
        "movdqu 256(%[v]), %%xmm4\n\t"
        "movdqu 320(%[v]), %%xmm5\n\t"
        "movdqu 384(%[v]), %%xmm6\n\t"
        "movdqu 448(%[v]), %%xmm7\n\t"
        "movdqu 512(%[v]), %%xmm8\n\t"
        "movdqu 576(%[v]), %%xmm9\n\t"
        "movdqu 640(%[v]), %%xmm10\n\t"
        "movdqu 704(%[v]), %%xmm11\n\t"
        "movdqu 768(%[v]), %%xmm12\n\t"
        "movdqu 832(%[v]), %%xmm13\n\t"
        "movdqu 896(%[v]), %%xmm14\n\t"
        "movdqu 960(%[v]), %%xmm15\n\t"
        "movl $1, (%[ready])\n\t1: pause\n\tjmp 1b"
        : : [v]"r"(ctx->vectors),[cw]"m"(ctx->control),[fp]"m"(ctx->extended),[mx]"m"(ctx->mxcsr),[ready]"r"(&ctx->ready)
        : "memory","cc","xmm0","xmm1","xmm2","xmm3","xmm4","xmm5","xmm6","xmm7","xmm8","xmm9","xmm10","xmm11","xmm12","xmm13","xmm14","xmm15");
    }
    __builtin_unreachable();
}
struct buffer {
    unsigned char *bytes;
    size_t size,capacity;
};
static void append(struct buffer *buffer,const void *data,size_t size) {
    if(buffer->size+size>buffer->capacity) {
        buffer->capacity=(buffer->size+size)*2+4096;
        buffer->bytes=realloc(buffer->bytes,buffer->capacity);
        REQUIRE(buffer->bytes);
    }
    memcpy(buffer->bytes+buffer->size,data,size);
    buffer->size+=size;
}
static void note(struct buffer *buffer,const char *owner,uint32_t type,const void *data,size_t size) {
    Elf64_Nhdr header= {
        .n_namesz=(Elf64_Word)strlen(owner)+1,.n_descsz=(Elf64_Word)size,.n_type=type
    };
    static const unsigned char zero[4]= {
        0
    };
    append(buffer,&header,sizeof(header));
    append(buffer,owner,header.n_namesz);
    append(buffer,zero,(4-buffer->size%4)%4);
    append(buffer,data,size);
    append(buffer,zero,(4-buffer->size%4)%4);
}
static void save(const char *root,const char *name,const void *data,size_t size) {
    char path[4096];
    int n=snprintf(path,sizeof(path),"%s/%s",root,name);
    REQUIRE(n>0 && (size_t)n<sizeof(path));
    int fd=open(path,O_CREAT|O_EXCL|O_WRONLY|O_CLOEXEC,0600);
    REQUIRE(fd>=0);
    const unsigned char *next=data;
    while(size) {
        ssize_t written=write(fd,next,size);
        REQUIRE(written>0);
        next+=written;
        size-=(size_t)written;
    }
    REQUIRE(close(fd)==0);
}
static size_t regset(pid_t tid,unsigned type,unsigned char *output,size_t capacity) {
    struct iovec vector= {
        output,capacity
    };
    REQUIRE(ptrace(PTRACE_GETREGSET,tid,(void *)(uintptr_t)type,&vector)==0);
    return vector.iov_len;
}
static void capture(struct buffer *notes,const char *root,pid_t tid,unsigned index,int wide) {
    unsigned char registers[512]= {
        0
    }
    ,floating[512]= {
        0
    }
    ,extended[65536]= {
        0
    }
    ,status[336]= {
        0
    };
    char name[64];
    size_t n=regset(tid,NT_PRSTATUS,registers,sizeof(registers));
    REQUIRE(n==(wide?216:68));
    memcpy(status+(wide?32:24),&tid,sizeof(tid));
    memcpy(status+(wide?112:72),registers,n);
    uint32_t valid=1;
    memcpy(status+(wide?328:140),&valid,4);
    size_t status_size=wide?336:144;
    note(notes,"CORE",NT_PRSTATUS,status,status_size);
    snprintf(name,sizeof(name),"thread-%u.prstatus",index);
    save(root,name,status,status_size);
    n=regset(tid,NT_FPREGSET,floating,sizeof(floating));
    REQUIRE(n==(wide?512:108));
    note(notes,"CORE",NT_FPREGSET,floating,n);
    snprintf(name,sizeof(name),"thread-%u.fpreg",index);
    save(root,name,floating,n);
    if(!wide) {
        unsigned char fpx[512]= {
            0
        };
        if(ptrace(PTRACE_GETFPXREGS,tid,NULL,fpx)==0) {
            note(notes,"LINUX",NT_PRXFPREG,fpx,sizeof(fpx));
            snprintf(name,sizeof(name),"thread-%u.fpx",index);
            save(root,name,fpx,sizeof(fpx));
        }
    }
    struct iovec vector= {
        extended,sizeof(extended)
    };
    if(ptrace(PTRACE_GETREGSET,tid,(void *)(uintptr_t)NT_X86_XSTATE,&vector)==0) {
        note(notes,"LINUX",NT_X86_XSTATE,extended,vector.iov_len);
        snprintf(name,sizeof(name),"thread-%u.xstate",index);
        save(root,name,extended,vector.iov_len);
    }
    printf("thread=%u tid=%d class=%u fp_bytes=%zu xstate_bytes=%zu\n",index,tid,wide?2:1,n,vector.iov_len);
    if(index==0) {
        unsigned char layout[4096]= {
            0
        };
        struct iovec layout_vector= {
            layout,sizeof(layout)
        };
        if(ptrace(PTRACE_GETREGSET,tid,(void *)(uintptr_t)0x205,&layout_vector)==0) {
            note(notes,"LINUX",0x205,layout,layout_vector.iov_len);
            save(root,"xstate-layout",layout,layout_vector.iov_len);
            printf("xstate_layout_bytes=%zu\n",layout_vector.iov_len);
        }
    }
}
int main(int argc,char **argv) {
    REQUIRE(argc==2 || argc==3);
    struct rlimit no_core= {
        0,0
    };
    REQUIRE(setrlimit(RLIMIT_CORE,&no_core)==0);
    alarm(30);
    REQUIRE(mkdir(argv[1],0700)==0);
    int wide=argc==2;
    struct buffer notes= {
        0
    };
    struct context *contexts=mmap(NULL,sizeof(struct context)*3,PROT_READ|PROT_WRITE,MAP_SHARED|MAP_ANONYMOUS,-1,0);
    REQUIRE(contexts!=MAP_FAILED);
    for(unsigned i=0;i<3;i++) {
        contexts[i].index=i;
        contexts[i].mode=(__builtin_cpu_supports("avx512f") && __builtin_cpu_supports("avx512bw"))?2:__builtin_cpu_supports("avx")?1:0;
        contexts[i].control=0x077f;
        contexts[i].mxcsr=0x3f80;
        contexts[i].mask=UINT64_C(0x13579bdf2468ace0)+i;
        uint64_t significand=UINT64_C(0xc000000000000000)+(uint64_t)i*UINT64_C(0x1000000000000000);
        uint16_t exponent=0x3fff;
        memcpy(contexts[i].extended,&significand,8);
        memcpy(contexts[i].extended+8,&exponent,2);
        for(unsigned r=0;r<32;r++)for(unsigned b=0;b<64;b++)contexts[i].vectors[r][b]=(unsigned char)(0x21+i*0x31+r*7+b);
    }
    owned_child=fork();
    REQUIRE(owned_child>=0);
    if(owned_child==0) {
        REQUIRE(prctl(PR_SET_PDEATHSIG,SIGKILL)==0);
        REQUIRE(getppid()>1);
        if(!wide) {
            REQUIRE(ptrace(PTRACE_TRACEME,0,NULL,NULL)==0);
            execl(argv[2],argv[2],NULL);
            _exit(125);
        }
        pthread_t workers[2];
        REQUIRE(pthread_create(&workers[0],NULL,register_loop,&contexts[1])==0);
        REQUIRE(pthread_create(&workers[1],NULL,register_loop,&contexts[2])==0);
        register_loop(&contexts[0]);
    }
    if(wide) {
        for(unsigned index=0;index<3;index++) {
            unsigned tries=0;
            while(!atomic_load_explicit(&contexts[index].ready,memory_order_acquire)) {
                REQUIRE(++tries<10000);
                usleep(1000);
            }
        }
        for(unsigned index=0;index<3;index++)REQUIRE(ptrace(PTRACE_ATTACH,contexts[index].tid,NULL,NULL)==0);
        for(unsigned index=0;index<3;index++) {
            int status;
            REQUIRE(waitpid(contexts[index].tid,&status,__WALL)==contexts[index].tid && WIFSTOPPED(status));
        }
        for(unsigned index=0;index<3;index++)capture(&notes,argv[1],contexts[index].tid,index,wide);
    }
    else {
        int status;
        REQUIRE(waitpid(owned_child,&status,0)==owned_child && WIFSTOPPED(status));
        REQUIRE(ptrace(PTRACE_CONT,owned_child,NULL,NULL)==0);
        REQUIRE(waitpid(owned_child,&status,0)==owned_child && WIFSTOPPED(status) && WSTOPSIG(status)==SIGTRAP);
        capture(&notes,argv[1],owned_child,0,wide);
    }
    cleanup();
    uint64_t file64[2]= {
        0,4096
    };
    uint32_t file32[2]= {
        0,4096
    };
    note(&notes,"CORE",NT_FILE,wide?(const void*)file64:(const void*)file32,wide?sizeof(file64):sizeof(file32));
    struct buffer core= {
        0
    };
    if(wide) {
        Elf64_Ehdr header= {
            .e_ident= {
                ELFMAG0,ELFMAG1,ELFMAG2,ELFMAG3,ELFCLASS64,ELFDATA2LSB,EV_CURRENT
            }
            ,.e_type=ET_CORE,.e_machine=EM_X86_64,.e_version=EV_CURRENT,.e_phoff=sizeof(Elf64_Ehdr),.e_ehsize=sizeof(Elf64_Ehdr),.e_phentsize=sizeof(Elf64_Phdr),.e_phnum=1
        };
        Elf64_Phdr ph= {
            .p_type=PT_NOTE,.p_offset=sizeof(header)+sizeof(ph),.p_filesz=notes.size,.p_memsz=notes.size,.p_align=4
        };
        append(&core,&header,sizeof(header));
        append(&core,&ph,sizeof(ph));
    }
    else {
        Elf32_Ehdr header= {
            .e_ident= {
                ELFMAG0,ELFMAG1,ELFMAG2,ELFMAG3,ELFCLASS32,ELFDATA2LSB,EV_CURRENT
            }
            ,.e_type=ET_CORE,.e_machine=EM_386,.e_version=EV_CURRENT,.e_phoff=sizeof(Elf32_Ehdr),.e_ehsize=sizeof(Elf32_Ehdr),.e_phentsize=sizeof(Elf32_Phdr),.e_phnum=1
        };
        Elf32_Phdr ph= {
            .p_type=PT_NOTE,.p_offset=sizeof(header)+sizeof(ph),.p_filesz=notes.size,.p_memsz=notes.size,.p_align=4
        };
        append(&core,&header,sizeof(header));
        append(&core,&ph,sizeof(ph));
    }
    append(&core,notes.bytes,notes.size);
    save(argv[1],"registers.elf",core.bytes,core.size);
    free(core.bytes);
    free(notes.bytes);
    REQUIRE(munmap(contexts,sizeof(struct context)*3)==0);
    return 0;
}
