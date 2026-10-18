#!/usr/bin/env python3
"""Build one copied vchroot unit exposing its in-call final metadata snapshot.

The object REPLACES vchroot_userspace.c.o; never link it beside the original.
Original vchroot entry points and prefix state are retained in this same unit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
from build_copy_signal_bridge import x86_64_flags

BASE = Path('src/external/xnu/darling/src/libsystem_kernel/emulation')
INPUT = BASE / 'src/linux_premigration/vchroot_userspace.c'


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'expected one source anchor: {old!r}')
    return text.replace(old, new, 1)


def patch_source(text):
    text = replace_once(text, '\tbool follow;\n', '''\tbool follow;
    /* Invocation-local snapshot, never reused across calls. The caller owns
     * initialized Linux x86_64 stat144 output; no guest pointer is accepted. */
    stat_t* metadata_output;
    bool metadata_valid;
''')
    text = replace_once(text, 'int vchroot_expand(struct vchroot_expand_args* args)\n{',
                        'static int vchroot_expand_impl(struct vchroot_expand_args* args, stat_t* metadata)\n{')
    text = replace_once(text, '\tctxt.unknown_component = false;', '''\tctxt.metadata_output = metadata;
    ctxt.metadata_valid = false;
    ctxt.unknown_component = false;''')
    anchor = '\treturn rv;\n}\n\nstatic int vchroot_run('
    text = replace_once(text, anchor, '''    if (rv != 0)
        return rv;
    return ctxt.metadata_valid ? 0 : 1;
}

/* Existing expansion ABI remains zero on every successful expansion. */
int vchroot_expand(struct vchroot_expand_args* args)
{
    int rv = vchroot_expand_impl(args, NULL);
    return rv < 0 ? rv : 0;
}

/* Private bridge: 0=expanded with final non-symlink metadata; 1=expanded,
 * caller must perform final stat; negative=original Linux expansion error.
 * Final syscall flags MUST still be converted/validated after this function.
 * Only flags 0/AT_SYMLINK_NOFOLLOW may reuse the returned snapshot. */
int vchroot_expand_stat(struct vchroot_expand_args* args, stat_t* metadata)
{
    return vchroot_expand_impl(args, metadata);
}

static int vchroot_run(''')
    text = replace_once(text, '\t\tconst unsigned int len = end - input_path;', '''        /* '.', '..', root changes and recursion cannot leave an earlier
         * component's metadata marked as the final result. */
        ctxt->metadata_valid = false;
        const unsigned int len = end - input_path;''')
    anchor = '\n\t\t\t\t\tif (icase_enabled && status == -LINUX_ENOENT)'
    text = replace_once(text, anchor, '''
                    if (ctxt->metadata_output && status == 0 && *end == '\\0'
                        && (st.st_mode & LINUX_S_IFMT) != LINUX_S_IFLNK)
                    {
                        /* Successful Linux stat initializes its ABI output.
                         * Restrict reuse to the actual last ordinary component;
                         * case corrections, symlinks and trailing '/' fall back. */
                        memcpy(ctxt->metadata_output, &st, sizeof(st));
                        ctxt->metadata_valid = true;
                    }
''' + anchor)
    anchor = '\t\t\t\t\t\t\trv = vchroot_run(link, ctxt);'
    text = replace_once(text, anchor, anchor + '''
                            /* The conservative fast path never returns a
                             * snapshot obtained through final-link recursion. */
                            ctxt->metadata_valid = false;''')
    anchor = "\t\t\t\t\t\tctxt->current_path[0] = '\\0';"
    text = replace_once(text, anchor, '                        ctxt->metadata_valid = false;\n' + anchor)
    return patch_bounds(text)


def patch_bounds(text):
    text = replace_once(text, "#\tdefine LINUX_ELOOP ELOOP", "#\tdefine LINUX_ELOOP ELOOP\n#\tdefine LINUX_ENOTDIR ENOTDIR")
    length_helper = r"""
/* A bounded scan is used only for fixed-size runtime buffers or the original
 * readable C pathname contract; it never changes the guest-pointer boundary. */
static __SIZE_TYPE__ dearust_vchroot_length(const char* path, __SIZE_TYPE__ limit)
{
    __SIZE_TYPE__ length = 0;
    while (length < limit && path[length])
        length++;
    return length;
}
"""
    text = replace_once(text, 'static bool next_component(const char* from, const char** end);',
                        'static bool next_component(const char* from, const char** end);' + length_helper)
    text = text.replace('buf, prefix_path, sizeof(prefix_path) - 1)',
                        'buf, prefix_path, sizeof(prefix_path))')
    text = replace_once(text, "\tprefix_path[rv] = '\\0';", r"""    if (rv < 0)
        return rv;
    if ((__SIZE_TYPE__)rv >= sizeof(prefix_path))
        return -LINUX_ENAMETOOLONG;
    prefix_path[rv] = '\0';""")
    text = replace_once(text, '\tstrcpy(a.path, path);', r"""    __SIZE_TYPE__ input_length = dearust_vchroot_length(path, sizeof(a.path));
    if (input_length == sizeof(a.path))
        return -LINUX_ENAMETOOLONG;
    memcpy(a.path, path, input_length + 1);""")
    text = replace_once(text, '\tprefix_path_len = (int)tmp_len;', r"""    if (tmp_len >= sizeof(prefix_path))
        __simple_abort();
    prefix_path[tmp_len] = '\0';
    if (dearust_vchroot_length(prefix_path, sizeof(prefix_path)) != tmp_len
        || (tmp_len && prefix_path[0] != '/'))
        __simple_abort();
    prefix_path_len = (int)tmp_len;""")
    text = replace_once(text, '\tconst char* input_path = args->path;', r"""    if (dearust_vchroot_length(args->path, sizeof(args->path)) == sizeof(args->path))
        return -LINUX_ENAMETOOLONG;
    const char* input_path = args->path;""")
    text = text.replace('buf, ctxt.current_path, sizeof(ctxt.current_path) - 1)',
                        'buf, ctxt.current_path, sizeof(ctxt.current_path))')
    text = replace_once(text, '\t\t// If we got "/",', r"""        if ((__SIZE_TYPE__)rv >= sizeof(ctxt.current_path))
            return -LINUX_ENAMETOOLONG;
        /* procfs descriptor targets without an absolute path are not dirs. */
        if (rv == 0 || ctxt.current_path[0] != '/')
            return -LINUX_ENOTDIR;

        // If we got "/",""")
    slash = "\t\t\t// append /\n\t\t\tctxt->current_path[ctxt->current_path_len++] = '/';\n\t\t\tctxt->current_path[ctxt->current_path_len] = '\\0';\n"
    text = replace_once(text, slash, '')
    text = replace_once(text, '\t\t\t\tif (ctxt->current_path_len <= 1)',
                        '\t\t\t\tif (ctxt->current_path_len == 0)')
    text = replace_once(text, '\t\t\t\t\tctxt->current_path_len = prefix_path_len + sizeof(EXIT_PATH);',
                        '\t\t\t\t\tctxt->current_path_len = prefix_path_len + sizeof(EXIT_PATH) - 1;')
    text = replace_once(text, "\t\t\t\t\tctxt->current_path[ctxt->current_path_len-1] = '/';\n", '')
    text = replace_once(text, 'char* p = ctxt->current_path + ctxt->current_path_len - 2;',
                        'char* p = ctxt->current_path + ctxt->current_path_len - 1;')
    text = text.replace('// current_path always ends with a slash\n\t\t\t\t// p points before the last /',
                        '// Scan the existing path directly; do not append a transient\n                // slash that would overflow a valid 4095-byte directory path.')
    text = replace_once(text, '\t\t\t\t\tstrcpy(ctxt->current_path, prefix_path);', r"""                    if (prefix_path_len < 0 || (__SIZE_TYPE__)prefix_path_len
                        > sizeof(ctxt->current_path) - sizeof(EXIT_PATH))
                        return -LINUX_ENAMETOOLONG;
                    strcpy(ctxt->current_path, prefix_path);""")
    text = replace_once(text, "\t\t\t\t\tp--;", r"""                    if (p == ctxt->current_path)
                        return -LINUX_ENOTDIR;
                    p--;""")
    text = replace_once(text, '\t\t\t\tif (len + ctxt->current_path_len + 2 > sizeof(ctxt->current_path))\n\t\t\t\t\treturn -LINUX_ENOENT;', r"""                if (ctxt->current_path_len < 0
                    || (__SIZE_TYPE__)ctxt->current_path_len > sizeof(ctxt->current_path) - 2)
                    return -LINUX_ENAMETOOLONG;
                ctxt->current_path[ctxt->current_path_len++] = '/';
                ctxt->current_path[ctxt->current_path_len] = '\0';
                if (len + ctxt->current_path_len + 1 > sizeof(ctxt->current_path))
                    return -LINUX_ENAMETOOLONG;""")
    text = replace_once(text, '\t\t\t\t\t\t\t\t\tstrcpy(ctxt->current_path + prevlen, de->d_name);', r"""                                    __SIZE_TYPE__ actual_length = strlen(de->d_name);
                                    if (actual_length >= sizeof(ctxt->current_path) - prevlen) {
                                        LINUX_SYSCALL(__NR_close, dfd);
                                        return -LINUX_ENAMETOOLONG;
                                    }
                                    strcpy(ctxt->current_path + prevlen, de->d_name);
                                    ctxt->current_path_len = prevlen + actual_length;""")
    text = replace_once(text, '\t\t\t\t\t\t\tchar link[512];', '\t\t\t\t\t\t\tchar link[4096];')
    text = text.replace('ctxt->current_path, link, sizeof(link) - 1)',
                        'ctxt->current_path, link, sizeof(link))')
    text = replace_once(text, "\t\t\t\t\t\t\tlink[rv] = '\\0';", r"""                            if ((__SIZE_TYPE__)rv >= sizeof(link))
                                return -LINUX_ENAMETOOLONG;
                            link[rv] = '\0';""")
    text = text.replace('buf, link, sizeof(link) - 1)', 'buf, link, sizeof(link))')
    text = replace_once(text, "\tlink[rv] = '\\0';", r"""    if ((__SIZE_TYPE__)rv >= sizeof(link))
        return -LINUX_ENAMETOOLONG;
    link[rv] = '\0';""")
    text = replace_once(text, '\tif (rv >= prefix_path_len && strncmp(link, prefix_path, prefix_path_len) == 0)',
                        "\tif (rv >= prefix_path_len && strncmp(link, prefix_path, prefix_path_len) == 0\n        && (prefix_path_len == 0 || rv == prefix_path_len || link[prefix_path_len] == '/'))")
    text = replace_once(text, '\t\tif (args->maxlen-1 < rv - prefix_path_len)',
                        '\t\tunsigned required = rv == prefix_path_len ? 2u : (unsigned)(rv - prefix_path_len) + 1u;\n        if (args->maxlen < required)')
    text = replace_once(text, '\tif (memcmp(args->path, prefix_path, prefix_path_len) == 0)', r"""    __SIZE_TYPE__ path_length = dearust_vchroot_length(args->path, sizeof(args->path));
    if (path_length == sizeof(args->path))
        return -LINUX_ENAMETOOLONG;
    if (path_length >= (__SIZE_TYPE__)prefix_path_len
        && memcmp(args->path, prefix_path, prefix_path_len) == 0
        && (prefix_path_len == 0 || path_length == (__SIZE_TYPE__)prefix_path_len
            || args->path[prefix_path_len] == '/'))""")
    text = replace_once(text, '\t\tint bytes = strlen(args->path) + 1;', r"""        if (path_length > sizeof(args->path) - sizeof(EXIT_PATH))
            return -LINUX_ENAMETOOLONG;
        int bytes = path_length + 1;""")
    directory_helper = r"""
#ifndef LINUX_S_IFDIR
#define LINUX_S_IFDIR 0040000
#endif
/* Dot normalization must first establish that the current component is a
 * directory. In particular a regular-file dirfd or /file/.. is ENOTDIR. */
static int dearust_vchroot_directory(const char* path)
{
    stat_t st;
#if defined(__NR_stat)
    int status = LINUX_SYSCALL(__NR_stat, path, &st);
#else
    int status = LINUX_SYSCALL(__NR_newfstatat, LINUX_AT_FDCWD, path, &st, 0);
#endif
    if (status < 0)
        return status;
    return (st.st_mode & LINUX_S_IFMT) == LINUX_S_IFDIR ? 0 : -LINUX_ENOTDIR;
}
"""
    text = replace_once(text, 'static int vchroot_run(const char* path, struct context* ctxt);',
                        'static int vchroot_run(const char* path, struct context* ctxt);' + directory_helper)
    text = replace_once(text, '        const unsigned int len = end - input_path;', r"""        const unsigned int len = end - input_path;
        if ((len == 1 && input_path[0] == '.')
            || (len == 2 && input_path[0] == '.' && input_path[1] == '.'))
        {
            int directory_status = dearust_vchroot_directory(
                ctxt->current_path_len ? ctxt->current_path : "/");
            if (directory_status < 0)
                return directory_status;
        }""")
    # Root changes are transactions: a failed local lookup or server rejection
    # must leave the previous local prefix untouched.
    begin = text.index('int __darling_vchroot(int dfd)\n{')
    end = text.index('\n}', begin) + 2
    root = text[begin:end]
    rpc = """#ifndef TEST
	rv = errno_linux_to_bsd(dserver_rpc_vchroot(dfd));
	if (rv != 0)
		return rv;
#endif"""
    root = replace_once(root, rpc, r'''    stat_t directory;
#if defined(__NR_fstat64)
    rv = LINUX_SYSCALL(__NR_fstat64, dfd, &directory);
#else
    rv = LINUX_SYSCALL(__NR_fstat, dfd, &directory);
#endif
    if (rv < 0)
        return dearust_vchroot_root_error(rv);
    if ((directory.st_mode & LINUX_S_IFMT) != LINUX_S_IFDIR)
        return dearust_vchroot_root_error(-LINUX_ENOTDIR);''')
    root = replace_once(root, 'char buf[50];', 'char buf[50];\n    char resolved[sizeof(prefix_path)];')
    root = root.replace('buf, prefix_path, sizeof(prefix_path)', 'buf, resolved, sizeof(resolved)')
    root = replace_once(root, 'return rv;\n    if ((__SIZE_TYPE__)rv >= sizeof(prefix_path))',
                        'return dearust_vchroot_root_error(rv);\n    if ((__SIZE_TYPE__)rv >= sizeof(prefix_path))')
    root = replace_once(root, 'return -LINUX_ENAMETOOLONG;',
                        'return dearust_vchroot_root_error(-LINUX_ENAMETOOLONG);')
    root = replace_once(root, "prefix_path[rv] = '\\0';", r"""resolved[rv] = '\0';
#if !defined(TEST) || defined(DEARUST_TEST_ROOT_RPC)
    int status = errno_linux_to_bsd(dserver_rpc_vchroot(dfd));
    if (status != 0)
        return status;
#endif
    memcpy(prefix_path, resolved, (__SIZE_TYPE__)rv + 1);""")
    helper = """static int dearust_vchroot_root_error(int error)
{
#ifdef TEST
    return error;
#else
    return errno_linux_to_bsd(error);
#endif
}

"""
    attribute = '#ifndef TEST\nVISIBLE\n#endif\n'
    if not text[:begin].endswith(attribute):
        raise ValueError('expected original root export attribute')
    text = text[:begin - len(attribute)] + helper + attribute + root + text[end:]
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    build = args.build.resolve(strict=True)
    source = next(Path(line.partition('=')[2]).resolve() for line in
                  (build / 'CMakeCache.txt').read_text().splitlines()
                  if line.startswith('CMAKE_HOME_DIRECTORY:INTERNAL='))
    output = args.output.resolve()
    if output.is_relative_to(build) or output.is_relative_to(source):
        parser.error('output must be outside reference source/build')
    directory = build / BASE
    flags_file = directory / 'CMakeFiles/emulation.dir/flags.make'
    flags = dict(line.split(' = ', 1) for line in flags_file.read_text().splitlines()
                 if ' = ' in line)
    compiler = ['/usr/bin/clang', *x86_64_flags(sum((shlex.split(flags[key]) for key in
                ['C_DEFINES', 'C_INCLUDES', 'C_FLAGS']), []))]
    original = source / INPUT
    raw = original.read_bytes()
    helper = Path(__file__).resolve().parent / 'fixtures/vchroot-at-bridge.h'
    helper_raw = helper.read_bytes()
    patched = (patch_source(raw.decode()) + '\n#include "vchroot-at-bridge.h"\n').encode()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'vchroot-at-bridge.h').write_bytes(helper_raw)
    copied = output / 'vchroot_userspace.c'
    copied.write_bytes(patched)
    obj = output / 'vchroot_userspace.c.o'
    command = [*compiler, '-c', str(copied), '-o', str(obj)]
    result = subprocess.run(command, cwd=directory, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (output / 'build.log').write_text(result.stdout)
    result.check_returncode()
    undefined = subprocess.check_output(['llvm-nm', '--undefined-only', str(obj)], text=True)
    (output / 'undefined.txt').write_text(undefined)
    (output / 'verification.json').write_text(json.dumps({
        'source': str(original), 'source_sha256': hashlib.sha256(raw).hexdigest(),
        'patched_sha256': hashlib.sha256(patched).hexdigest(),
        'helper_source': str(helper), 'helper_sha256': hashlib.sha256(helper_raw).hexdigest(),
        'object_sha256': hashlib.sha256(obj.read_bytes()).hexdigest(),
        'command': command, 'replace_only': str(INPUT) + '.o',
        'return_contract': '0=snapshot,1=expanded-fallback,negative=Linux-expansion-error',
        'snapshot_scope': 'same-call successful final ordinary non-symlink lstat; no persistent cache',
    }, indent=2) + '\n')


if __name__ == '__main__':
    main()
