/* Included at the end of the copied vchroot translation unit only. No guest
 * pointer is accepted: args is the caller's initialized, bounded local copy.
 * Keep the final operation relative to the SAME caller-owned directory fd.
 * This is deliberately restricted to non-following single-component requests. */
#ifndef DEARUST_VCHROOT_AT_BRIDGE_H
#define DEARUST_VCHROOT_AT_BRIDGE_H
#ifdef TEST
#define DEARUST_AT_NOFOLLOW AT_SYMLINK_NOFOLLOW
#else
#define DEARUST_AT_NOFOLLOW LINUX_AT_SYMLINK_NOFOLLOW
#endif

int vchroot_expand_at(struct vchroot_expand_args* args)
{
    if (args->flags != 0 || args->dfd < 0)
        return vchroot_expand(args);
    __SIZE_TYPE__ length = 0;
    while (length < sizeof(args->path) && args->path[length]) {
        if (args->path[length] == '/')
            return vchroot_expand(args);
        length++;
    }
    if (length == 0 || length == sizeof(args->path)
        || (length == 1 && args->path[0] == '.')
        || (length == 2 && args->path[0] == '.' && args->path[1] == '.')
        /* This basename can complete the virtual /Volumes/SystemRoot escape
         * when dfd refers to the prefix's Volumes directory. */
        || strcasecmp(args->path, "SystemRoot") == 0)
        return vchroot_expand(args);
#ifndef TEST
    if (prefix_path_len == -1)
        init_vchroot_path();
#endif
    stat_t metadata;
#if defined(__NR_newfstatat)
    int status = LINUX_SYSCALL(__NR_newfstatat, args->dfd, args->path,
                               &metadata, DEARUST_AT_NOFOLLOW);
#elif defined(__NR_fstatat64)
    int status = LINUX_SYSCALL(__NR_fstatat64, args->dfd, args->path,
                               &metadata, DEARUST_AT_NOFOLLOW);
#else
    return vchroot_expand(args);
#endif
#if defined(__NR_newfstatat) || defined(__NR_fstatat64)
    if (status == 0)
        return 0;
    /* Missing/wrong-case names retain the entire existing case-fold resolver.
     * In particular, this helper allocates no temporary descriptor and creates
     * no new fork/exec descriptor lifetime. All errors preserve fallback policy. */
    return vchroot_expand(args);
#endif
}
#undef DEARUST_AT_NOFOLLOW
#endif
