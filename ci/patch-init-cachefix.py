#!/usr/bin/env python3
"""patch-init-cachefix.py -- the shared cache check was wrong, and what launchd inherits.

The cache probe checks the main cache file like this:

    check(stat(paths[2], &cst) == 0 && cst.st_size > 1000000,
        "the shared cache is where dyld looks for it");

and on this image that is red while the cache is complete. The main file of a split dyld cache is
only the header and the list of subcache files -- 393,216 bytes here, magic "dyld_v1   arm64" --
and the 2.5 GB that dyld maps live in dyld_shared_cache_arm64.01 ... .42 next to it. A check that
wants the main file over 1 MB is a check for a monolithic cache, so it fails on every split one
and sends the hunt for the init-with-data-volume stop the wrong way.

This patch:

  1. replaces that check with one that matches what a split cache looks like: magic "dyld_v1" in
     the main file, at least 40 cache files, and more than 1 GB in them (a complete cache);
  2. at the end of flash_root (after the switch root, before main hands process 1 to launchd)
     prints the five cache paths with their stat, lists the cache directory, and prints the state
     the boot tasks around init-with-data-volume will find: /System/Volumes/Data and
     /private/var, whether /private/var and /private/var/db are writable, and launchd's own
     files (xpc/launchd.plist, LaunchDaemons, /sbin/launchd.darwin, /usr/lib/dyld).

    python3 ci/patch-init-cachefix.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-CACHEFIX"

# list_tree() is defined further down than flash_root(); a forward declaration lets the report
# below call it. (The compiler is C99: an implicit declaration would be an error, not a warning.)
DECL_ANCHOR = "static void check(int ok, const char *what);\n"

DECL_NEW = DECL_ANCHOR + "static int list_tree(const char *path, int depth);\n"

CHECK_ANCHOR = """\t\tcheck(stat(paths[2], &cst) == 0 && cst.st_size > 1000000,
\t\t    "the shared cache is where dyld looks for it");
"""

CHECK_NEW = """\t\t/*
\t\t * HAKIOS-CACHEFIX: the check here used to want the main file over 1 MB. A split dyld
\t\t * cache's main file is only the header and the list of subcaches -- 393,216 bytes
\t\t * here -- and the 2.5 GB that dyld maps are in dyld_shared_cache_arm64.01 ... .42
\t\t * beside it. So the old check failed on a cache that is complete. Check the magic and
\t\t * the subcaches: the main file alone says nothing.
\t\t */
\t\t{
\t\t\tstatic const char *const cf_dirs[] = {
\t\t\t\t"/System/Library/Caches/com.apple.dyld",
\t\t\t\t"/System/Cryptexes/OS/System/Library/Caches/com.apple.dyld",
\t\t\t};
\t\t\tstatic char cf_buf[8192];
\t\t\tchar cf_magic[16], cf_child[256];
\t\t\tconst char *cf_dir = NULL;
\t\t\toff_t cf_pos = 0, cf_total = 0;
\t\t\tssize_t cf_n;
\t\t\tint cf_files = 0, cf_magic_ok = 0, cf_fd = -1, cf_mfd;
\t\t\tunsigned cf_j;

\t\t\tcf_mfd = open(paths[2], O_RDONLY, 0);
\t\t\tcf_n = cf_mfd >= 0 ? read(cf_mfd, cf_magic, sizeof(cf_magic)) : -1;
\t\t\tif (cf_mfd >= 0) {
\t\t\t\tclose(cf_mfd);
\t\t\t}
\t\t\tif (cf_n == (ssize_t)sizeof(cf_magic) && memcmp(cf_magic, "dyld_v1", 7) == 0) {
\t\t\t\tcf_magic_ok = 1;
\t\t\t}
\t\t\tfor (cf_j = 0; cf_j < sizeof(cf_dirs) / sizeof(cf_dirs[0]) && cf_fd < 0; cf_j++) {
\t\t\t\tcf_fd = open(cf_dirs[cf_j], O_RDONLY | O_DIRECTORY, 0);
\t\t\t\tif (cf_fd >= 0) {
\t\t\t\t\tcf_dir = cf_dirs[cf_j];
\t\t\t\t}
\t\t\t}
\t\t\twhile (cf_fd >= 0 && (cf_n = getdirentries(cf_fd, cf_buf, sizeof(cf_buf), &cf_pos)) > 0) {
\t\t\t\tfor (ssize_t cf_at = 0; cf_at < cf_n;) {
\t\t\t\t\tstruct dirent *cf_de = (struct dirent *)(void *)(cf_buf + cf_at);

\t\t\t\t\tif (cf_de->d_reclen == 0) {
\t\t\t\t\t\tbreak;
\t\t\t\t\t}
\t\t\t\t\tcf_at += cf_de->d_reclen;
\t\t\t\t\tif (strncmp(cf_de->d_name, "dyld_shared_cache", 17) != 0) {
\t\t\t\t\t\tcontinue;
\t\t\t\t\t}
\t\t\t\t\tif (strlen(cf_dir) + strlen(cf_de->d_name) + 2 > sizeof(cf_child)) {
\t\t\t\t\t\tcontinue;
\t\t\t\t\t}
\t\t\t\t\tstrcpy(cf_child, cf_dir);
\t\t\t\t\tstrcpy(cf_child + strlen(cf_child), "/");
\t\t\t\t\tstrcpy(cf_child + strlen(cf_child), cf_de->d_name);
\t\t\t\t\tif (stat(cf_child, &cst) == 0) {
\t\t\t\t\t\tcf_total += (off_t)cst.st_size;
\t\t\t\t\t\tcf_files++;
\t\t\t\t\t}
\t\t\t\t}
\t\t\t}
\t\t\tif (cf_fd >= 0) {
\t\t\t\tclose(cf_fd);
\t\t\t}
\t\t\tprintf("  cachefix: %s: %d files, %lld MB, main file magic %s\\n",
\t\t\t    cf_dir != NULL ? cf_dir : "(no cache directory)", cf_files,
\t\t\t    (long long)(cf_total >> 20), cf_magic_ok ? "dyld_v1 (ok)" : "NOT a dyld cache");
\t\t\tcheck(cf_magic_ok && cf_files >= 40 && cf_total > (off_t)(1LL << 30),
\t\t\t    "the shared cache is where dyld looks for it");
\t\t}
"""

# flash_root() ends with flash_root_mode = 1. The report goes just before it: after the switch
# root, after the cryptexes and the binds, and before main hands process 1 to launchd.
REPORT_ANCHOR = "\tflash_root_mode = 1;\n"

REPORT_NEW = """\t/*
\t * HAKIOS-CACHEFIX: the cache is complete (the main file is a split cache's header; the
\t * 2.5 GB are in the subcache files next to it), so nothing here changes paths. What is
\t * printed is the state launchd's boot tasks -- the ones around init-with-data-volume --
\t * inherit: where the cache really is, whether the data volume and a writable /private/var
\t * are there, and launchd's own files.
\t */
\t{
\t\tstatic const char *const cf_probe[] = {
\t\t\t"/private/preboot/Cryptexes/OS/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64",
\t\t\t"/System/Cryptexes/OS/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64",
\t\t\t"/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64",
\t\t\t"/private/preboot/Cryptexes/OS/System/Library/Caches/com.apple.dyld",
\t\t\t"/System/Library/Caches/com.apple.dyld",
\t\t};
\t\tstatic const char *const cf_boot[] = {
\t\t\t"/System/Volumes/Data",
\t\t\t"/System/Volumes/Data/private/var",
\t\t\t"/private/var",
\t\t\t"/private/var/db",
\t\t\t"/private/var/db/launchd.db",
\t\t\t"/private/var/db/diagnostics",
\t\t\t"/System/Library/xpc/launchd.plist",
\t\t\t"/System/Library/xpc/launchd.plist.sig",
\t\t\t"/System/Library/LaunchDaemons",
\t\t\t"/sbin/launchd.darwin",
\t\t\t"/usr/lib/dyld",
\t\t\t"/System/Cryptexes/OS",
\t\t\t"/private/preboot/Cryptexes/OS",
\t\t};
\t\tstatic const char *const cf_write[] = {
\t\t\t"/private/var",
\t\t\t"/private/var/db",
\t\t};
\t\tchar cf_link[256], cf_path[256];
\t\tstruct stat cf_st;
\t\tssize_t cf_n;
\t\tunsigned cf_i;
\t\tint cf_fd, cf_err;

\t\tprintf("\\n-- cachefix: what launchd's boot tasks inherit --\\n");
\t\t{
\t\t\tconst char *cf_arg;

\t\t\tcf_arg = boot_arg("root_apfs");
\t\t\tprintf("  cachefix: boot-arg root_apfs=%s\\n", cf_arg != NULL ? cf_arg : "(none)");
\t\t\tcf_arg = boot_arg("var_hfs");
\t\t\tprintf("  cachefix: boot-arg var_hfs=%s\\n", cf_arg != NULL ? cf_arg : "(none)");
\t\t\tcf_arg = boot_arg("hakios_ramfs");
\t\t\tprintf("  cachefix: boot-arg hakios_ramfs=%s\\n", cf_arg != NULL ? cf_arg : "(none)");
\t\t}
\t\tcf_n = readlink("/System/Library/Caches/com.apple.dyld", cf_link, sizeof(cf_link) - 1);
\t\tcf_link[cf_n > 0 ? cf_n : 0] = 0;
\t\tprintf("  cachefix: /System/Library/Caches/com.apple.dyld -> %s\\n",
\t\t    cf_n > 0 ? cf_link : strerror(errno));
\t\tfor (cf_i = 0; cf_i < sizeof(cf_probe) / sizeof(cf_probe[0]); cf_i++) {
\t\t\tif (lstat(cf_probe[cf_i], &cf_st) == 0) {
\t\t\t\tprintf("  cachefix: %s -> %s %lld bytes\\n", cf_probe[cf_i],
\t\t\t\t    (cf_st.st_mode & S_IFMT) == S_IFDIR ? "dir" :
\t\t\t\t    (cf_st.st_mode & S_IFMT) == S_IFLNK ? "link" : "file",
\t\t\t\t    (long long)cf_st.st_size);
\t\t\t} else {
\t\t\t\tprintf("  cachefix: %s -> %s\\n", cf_probe[cf_i], strerror(errno));
\t\t\t}
\t\t}
\t\tprintf("  cachefix: %s:\\n", "/System/Library/Caches/com.apple.dyld");
\t\t(void)list_tree("/System/Library/Caches/com.apple.dyld", 0);
\t\tprintf("  cachefix: what the data-volume boot tasks need:\\n");
\t\tfor (cf_i = 0; cf_i < sizeof(cf_boot) / sizeof(cf_boot[0]); cf_i++) {
\t\t\tif (lstat(cf_boot[cf_i], &cf_st) == 0) {
\t\t\t\tprintf("  cachefix: %s -> %s %lld bytes\\n", cf_boot[cf_i],
\t\t\t\t    (cf_st.st_mode & S_IFMT) == S_IFDIR ? "dir" :
\t\t\t\t    (cf_st.st_mode & S_IFMT) == S_IFLNK ? "link" : "file",
\t\t\t\t    (long long)cf_st.st_size);
\t\t\t} else {
\t\t\t\tprintf("  cachefix: %s -> %s\\n", cf_boot[cf_i], strerror(errno));
\t\t\t}
\t\t}
\t\tfor (cf_i = 0; cf_i < sizeof(cf_write) / sizeof(cf_write[0]); cf_i++) {
\t\t\tcf_path[0] = 0;
\t\t\tif (strlen(cf_write[cf_i]) + 32 < sizeof(cf_path)) {
\t\t\t\tstrcpy(cf_path, cf_write[cf_i]);
\t\t\t\tstrcpy(cf_path + strlen(cf_path), "/.cachefix-probe");
\t\t\t}
\t\t\tcf_err = 0;
\t\t\tcf_fd = cf_path[0] != 0 ? open(cf_path, O_WRONLY | O_CREAT | O_EXCL, 0600) : -1;
\t\t\tif (cf_fd >= 0) {
\t\t\t\tclose(cf_fd);
\t\t\t\t(void)unlink(cf_path);
\t\t\t\tprintf("  cachefix: %s writable: yes\\n", cf_write[cf_i]);
\t\t\t} else {
\t\t\t\tcf_err = errno;
\t\t\t\tprintf("  cachefix: %s writable: no (%s)\\n", cf_write[cf_i], strerror(cf_err));
\t\t\t}
\t\t}
\t}
\tflash_root_mode = 1;
"""


def main(root):
    for rel in ("src/init.c", "init.c"):
        f = pathlib.Path(root) / rel
        if f.exists():
            break
    else:
        print("patch-init-cachefix: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-cachefix: already applied")
        return 0
    # Check every anchor before writing anything: a partial patch is worse than none.
    if text.count(DECL_ANCHOR) != 1:
        print("patch-init-cachefix: MISS: the check() forward declaration appears %d times"
              % text.count(DECL_ANCHOR))
        return 1
    if text.count(CHECK_ANCHOR) != 1:
        print("patch-init-cachefix: MISS: the shared-cache check appears %d times (is "
              "patch-init-cache-probe.py applied first?)" % text.count(CHECK_ANCHOR))
        return 1
    if text.count(REPORT_ANCHOR) != 1:
        print("patch-init-cachefix: MISS: flash_root_mode = 1 appears %d times"
              % text.count(REPORT_ANCHOR))
        return 1
    text = text.replace(DECL_ANCHOR, DECL_NEW, 1)
    text = text.replace(CHECK_ANCHOR, CHECK_NEW, 1)
    text = text.replace(REPORT_ANCHOR, REPORT_NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-cachefix: a split-cache check, and the boot-task state at the hand-over")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-cachefix.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
