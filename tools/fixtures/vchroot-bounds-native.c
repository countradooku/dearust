/* Run each original defect as a separate sanitizer process; patched cases must
 * return ENAMETOOLONG without touching a surrounding byte. Owned paths only. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
struct vchroot_expand_args {char path[4096];unsigned flags;int dfd;};
struct vchroot_unexpand_args {char path[4096];};
struct vchroot_fdpath_args {int fd;char *path;unsigned maxlen;};
extern int original_root(int),bridge_root(int);
extern int original_expand(struct vchroot_expand_args *),bridge_expand(struct vchroot_expand_args *);
extern int original_unexpand(struct vchroot_unexpand_args *),bridge_unexpand(struct vchroot_unexpand_args *);
extern int original_fdpath(struct vchroot_fdpath_args *),bridge_fdpath(struct vchroot_fdpath_args *);
static int fds[64];static char names[64][201];static unsigned depth;
static void cleanup(void){
    while(depth){assert(close(fds[depth])==0);assert(unlinkat(fds[depth-1],names[depth-1],AT_REMOVEDIR)==0);depth--;}
}
int main(int argc,char **argv){
    assert(argc==3);int original=strcmp(argv[1],"original")==0;
    int (*setroot)(int)=original?original_root:bridge_root;
    int (*expand)(struct vchroot_expand_args *)=original?original_expand:bridge_expand;
    int (*unexpand)(struct vchroot_unexpand_args *)=original?original_unexpand:bridge_unexpand;
    int (*fdpath)(struct vchroot_fdpath_args *)=original?original_fdpath:bridge_fdpath;
    char root[]="/tmp/dearust-vchroot-bounds-XXXXXX";assert(mkdtemp(root));
    fprintf(stderr,"OWNED_ROOT %s\n",root);
    fds[0]=open(root,O_DIRECTORY|O_RDONLY);assert(fds[0]>=0);assert(setroot(fds[0])==0);
    if(strcmp(argv[2],"append")==0 || strcmp(argv[2],"exit")==0){
        size_t length=strlen(root);
        while(length<4095){
            size_t n=4095-length-1;if(n>200)n=200;assert(n && depth<63);
            memset(names[depth],'a',n);names[depth][n]=0;
            assert(mkdirat(fds[depth],names[depth],0700)==0);
            fds[depth+1]=openat(fds[depth],names[depth],O_DIRECTORY|O_RDONLY);assert(fds[depth+1]>=0);
            length+=n+1;depth++;
        }
        char proc[64],actual[4096];snprintf(proc,sizeof(proc),"/proc/self/fd/%d",fds[depth]);
        assert(readlink(proc,actual,sizeof(actual))==4095);
        struct vchroot_expand_args args={.dfd=fds[depth]};strcpy(args.path,"file");
        if(strcmp(argv[2],"exit")==0){assert(setroot(fds[depth])==0);args.dfd=open("/",O_DIRECTORY|O_RDONLY);assert(args.dfd>=0);strcpy(args.path,"..");}
        int result=expand(&args);if(!original)assert(result==-ENAMETOOLONG);
        if(strcmp(argv[2],"exit")==0)assert(close(args.dfd)==0);
        cleanup();
    }else if(strcmp(argv[2],"unexpand")==0){
        struct {unsigned char head[16];struct vchroot_unexpand_args args;unsigned char tail[16];} out;
        memset(&out,0xa5,sizeof(out));memset(out.args.path,'x',4095);out.args.path[0]='/';out.args.path[4095]=0;
        int result=unexpand(&out.args);if(!original)assert(result==-ENAMETOOLONG);
        for(unsigned i=0;i<16;i++)assert(out.head[i]==0xa5 && out.tail[i]==0xa5);
    }else if(strcmp(argv[2],"fdzero")==0 || strcmp(argv[2],"fdroot")==0){
        unsigned char out[32];memset(out,0xa5,sizeof(out));struct vchroot_fdpath_args args={.fd=fds[0],.path=(char*)out+16,.maxlen=strcmp(argv[2],"fdzero")==0?0:1};
        int result=fdpath(&args);if(original){assert(result==0 && out[16]!=0xa5);puts("original incorrectly writes output despite insufficient capacity");}if(!original){assert(result==-ENAMETOOLONG);for(unsigned i=0;i<32;i++)assert(out[i]==0xa5);}
    }else if(strcmp(argv[2],"filedots")==0){
        int file=openat(fds[0],"file",O_CREAT|O_EXCL|O_RDONLY,0600);assert(file>=0);
        const char *paths[]={".","..","/file/../file","/file/."};
        for(unsigned i=0;i<4;i++){
            struct vchroot_expand_args args={.dfd=i<2?file:fds[0]};strcpy(args.path,paths[i]);
            int result=expand(&args);if(original)assert(result==0);else assert(result==-ENOTDIR);
        }
        struct vchroot_expand_args valid={.dfd=fds[0]};strcpy(valid.path,".");assert(expand(&valid)==0);
        assert(close(file)==0 && unlinkat(fds[0],"file",0)==0);
    }else if(strcmp(argv[2],"pipedirfd")==0){
        int pipefd[2];assert(pipe(pipefd)==0);
        struct vchroot_expand_args args={.dfd=pipefd[0]};strcpy(args.path,"..");
        int result=expand(&args);if(!original)assert(result==-ENOTDIR);
        assert(close(pipefd[0])==0 && close(pipefd[1])==0);
    }else if(strcmp(argv[2],"symlinklong")==0){
        char target[1024]={0};
        for(unsigned i=0;i<3;i++){
            memset(names[depth],'b',200);names[depth][200]=0;
            assert(mkdirat(fds[depth],names[depth],0700)==0);
            fds[depth+1]=openat(fds[depth],names[depth],O_DIRECTORY|O_RDONLY);assert(fds[depth+1]>=0);
            strcat(target,names[depth]);strcat(target,"/");depth++;
        }
        strcat(target,"file");int file=openat(fds[depth],"file",O_CREAT|O_EXCL|O_RDONLY,0600);assert(file>=0 && close(file)==0);
        assert(symlinkat(target,fds[0],"longlink")==0);
        struct vchroot_expand_args args={.dfd=fds[0],.flags=1};strcpy(args.path,"longlink");assert(expand(&args)==0);
        struct stat st;int result=stat(args.path,&st);if(original)assert(result==-1 && errno==ENOENT);else assert(result==0 && S_ISREG(st.st_mode));
        assert(unlinkat(fds[0],"longlink",0)==0 && unlinkat(fds[depth],"file",0)==0);cleanup();
    }else {assert(!"unknown case");}
    assert(close(fds[0])==0 && rmdir(root)==0);
    printf("%s %s bounds check completed\n",argv[1],argv[2]);
}
