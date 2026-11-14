#!/usr/bin/env python3
"""patch-init-xpcproxy.py -- deliver the patched xpcproxy through the payload, after the switch root.

The problem this ends: every user/501 service dies in xpcproxy with

    Service could not initialize: getpwnam(mobile), error 0x2 - No such file or directory
    xpcproxy exited due to exit(78)

xpcproxy is launchd's trampoline for jobs with a UserName; it calls getpwnam("mobile") at
vmaddr 0x100005A04 and, when libinfo's muser cannot answer (usermanagerd's XPC service is not
usable on this port), reports that through __xpc_fail and calls exit(78).  tools/patch-xpcproxy.py
--mode mobile rewrites six instruction words so xpcproxy skips the lookup, sets uid/gid 501 and
goes on to posix_spawnp: verified with llvm-objdump, --check, and an unpatch that restores the
original bytes (docs/XPROXY-PATCH-PLAN.md).

Why the patch does not go into the system image: /usr/libexec/xpcproxy lives on the read-only
Darwin volume, so the only way to change it there is to rebuild system.apfs, and the one boot
that did that panicked before launchd:

    Process 1 exec of /sbin/launchd failed, errno 8 (ENOEXEC)        (system18, docs/HANDOFF-2026-11-08-final.md)

so this patch uses the route the port already uses for the patched launchd.darwin and the
launchd jobs: the file travels in the payload (the RAM disk, mounted on /redwood while init
runs), and init puts it in front of the volume's file for this boot only.

Why /usr/libexec is bound, not /usr/libexec/xpcproxy: a mount point must be a directory --
mount(2) resolves the covered vnode and vfs_syscalls.c prepare_coveredvp() answers ENOTDIR for
anything that is not a VDIR -- so bindfs cannot place a file over a file.  What works is a
UNION mount (MNT_UNION) of the volume's /usr/libexec from a payload directory whose only entry
is xpcproxy: the name "xpcproxy" comes from the payload, and every other name is still found in
the volume's own directory (vfs_lookup.c, lookup_traverse_union(), reached when the lookup in
the mounted layer yields ENOENT).  The kernel is built DEVELOPMENT_ARM64_BCM2837 and that
configuration's FILESYS_DEV is [ FILESYS_BASE config_union_mounts fdesc ], so CONFIG_UNION_MOUNTS
is compiled in; MNT_UNION itself is in the mount flags mask of mount_common().

What this patch inserts, at the end of flash_root() (after kern.hakios_switch_root and after
/private/preboot/redwood has been bound on /redwood, before the hand-over in main()):

  1. prints /usr/libexec/xpcproxy as the volume has it (size, before);
  2. looks for the patched copy in the payload -- /redwood/patch/libexec/xpcproxy first, then
     /redwood/usr/libexec/xpcproxy (and the same under /private/preboot/redwood when /redwood
     is not the payload) -- and refuses any copy that does not carry the MARK
     "HAKIOS-XPCPROXY-PATCH" at file offset 0x8558 (tools/patch-xpcproxy.py writes it into
     __DATA_CONST slack; the binary is started with the marker in it, so a truncated or stale
     copy is not used);
  3. mount("bindfs", "/usr/libexec", MNT_RDONLY | MNT_UNION, "<the payload's directory>");
  4. reads the mounted file back for the MARK, and reads one sibling of xpcproxy
     (/usr/libexec/launchd_cache_loader) to prove the union falls through -- if either is wrong
     the mount is taken down again (unmount(2) = syscall 159), so a boot never continues on a
     /usr/libexec that has lost its other programs;
  5. one check() line reports the result:
         [ ok ] xpcproxy: the patched one over Darwin's (getpwnam skipped)

The payload copy is made by whoever rebuilds root.apfs, from the verified tool:

    python3 tools/patch-xpcproxy.py --mode mobile --out userland/root/patch/libexec/xpcproxy \\
        <a copy of sysroot.slim/usr/libexec/xpcproxy>
    # root.apfs: mkapfs ... redwood=root,manifest=obj/root.manifest usr=payload/usr

(the exact two-line recipe, the anchor, the boot-log check and the rollback are in
docs/XPROXY-DELIVERY.md; note that userland/build.sh starts with rm -rf root, so the copy step
belongs in whichever step packs root/, after that line).

Idempotent through MARK; every anchor is checked, and a MISS writes nothing and exits 1.

    python3 ci/patch-init-xpcproxy.py <userland dir>     (also takes the hakios tree root)

Exit: 0 applied (or already applied), 1 MISS, 2 usage.
"""
import pathlib
import sys

MARK = "HAKIOS-XPCPROXY-DELIVERY"

# The injection point: the last statement of flash_root(), i.e. after kern.hakios_switch_root and
# after the payload has been bound back on /redwood.  patch-init-cachefix.py and
# patch-init-jobbind.py use the same anchor and every one of them keeps it in its own insert, so
# this patch composes with them in any order (the blocks end up chained before this line).
ANCHOR = "\tflash_root_mode = 1;\n"

# Anchors that prove ANCHOR really is the end of flash_root() and not some other function's:
# the function starts once, and the switch_root sysctl call sits between the two.
FLASH_ROOT_ANCHOR = "static void\nflash_root(const char *dev)\n{\n"
SWITCH_ROOT_ANCHOR = 'sysctlbyname("kern.hakios_switch_root"'

BLOCK = '''\t/*
\t * HAKIOS-XPCPROXY-DELIVERY: the patched xpcproxy over Darwin's, from the payload -- the
\t * read-only system volume stays as Apple made it (a rebuilt system image is what panicked
\t * last time: "Process 1 exec of /sbin/launchd failed, errno 8", system18).
\t *
\t * xpcproxy is launchd's trampoline for jobs with a UserName, and for UserName=mobile it
\t * dies in getpwnam("mobile") -- libinfo's muser has no usable answer on this port -- with
\t * exit(78); this is the gate in front of SpringBoard and every other user/501 service.  Of
\t * the patched copy, tools/patch-xpcproxy.py --mode mobile: skip the lookup, uid/gid 501.
\t *
\t * The file cannot be bound over /usr/libexec/xpcproxy: a mount point must be a directory
\t * (vfs_syscalls.c, prepare_coveredvp(): ENOTDIR for anything but a VDIR).  So the volume's
\t * /usr/libexec is union-mounted (MNT_UNION) from the payload directory whose only entry is
\t * xpcproxy: that name comes from the payload, the rest still from the volume's own
\t * directory, which VFS falls back to when the mounted layer answers ENOENT
\t * (vfs_lookup.c, lookup_traverse_union(); CONFIG_UNION_MOUNTS is in the DEVELOPMENT_ARM64_
\t * BCM2837 configuration this kernel is built with, FILESYS_DEV -> config_union_mounts).
\t *
\t * Only a payload copy carrying the patch's MARK is used, and after the mount the target and
\t * one sibling are read back: a mount that fails either is taken down again, so the boot
\t * goes on with the volume's own /usr/libexec rather than a half-empty one.
\t *
\t * The payload copy: build/repack root.apfs with
\t *     userland/root/patch/libexec/xpcproxy   ->   /redwood/patch/libexec/xpcproxy
\t * made by "tools/patch-xpcproxy.py --mode mobile --out <that path>"; a copy left in
\t * userland/root/usr/libexec/ (-> /redwood/usr/libexec/) is accepted too.
\t */
\t{
\t\tstatic const char *const xp_root[] = {
\t\t\t"/redwood", "/private/preboot/redwood",
\t\t};
\t\tstatic const char *const xp_sub[] = {
\t\t\t"/patch/libexec", "/usr/libexec",
\t\t};
\t\tstatic const char xp_mark[] = "HAKIOS-XPCPROXY-PATCH";
\t\tenum {
\t\t\tXP_MARK_OFF = 0x8558,   /* MARK_VMADDR 0x100008558, __TEXT file offset == vmaddr */
\t\t\tXP_UNMOUNT = 159,       /* bsd/kern/syscalls.master: unmount(user_addr_t path, int flags) */
\t\t};
\t\tchar xp_dir[256], xp_file[320], xp_buf[64];
\t\tconst char *xp_from = NULL;
\t\tstruct stat xp_st;
\t\tssize_t xp_n;
\t\tunsigned xp_r, xp_s;
\t\tint xp_fd, xp_m = -1, xp_ours = 0, xp_whole = 0, xp_ok = 0;

\t\tprintf("\\n-- xpcproxy: the patched one over Darwin's --\\n");

\t\t/* as the volume has it before: Apple's 96,352-byte binary, no MARK */
\t\tif (stat("/usr/libexec/xpcproxy", &xp_st) == 0) {
\t\t\tprintf("  xpcproxy: before: /usr/libexec/xpcproxy, %lld bytes\\n",
\t\t\t    (long long)xp_st.st_size);
\t\t} else {
\t\t\tprintf("  xpcproxy: before: /usr/libexec/xpcproxy: %s\\n", strerror(errno));
\t\t}

\t\t/* the payload's copy: the first one that carries the patch's MARK */
\t\tfor (xp_r = 0; xp_r < sizeof(xp_root) / sizeof(xp_root[0]) && xp_from == NULL; xp_r++) {
\t\t\tfor (xp_s = 0; xp_s < sizeof(xp_sub) / sizeof(xp_sub[0]); xp_s++) {
\t\t\t\tstrcpy(xp_dir, join(xp_root[xp_r], xp_sub[xp_s], ""));
\t\t\t\tstrcpy(xp_file, join(xp_dir, "", "/xpcproxy"));
\t\t\t\txp_fd = open(xp_file, O_RDONLY, 0);
\t\t\t\tif (xp_fd < 0) {
\t\t\t\t\tprintf("  xpcproxy: %s: %s\\n", xp_file, strerror(errno));
\t\t\t\t\tcontinue;
\t\t\t\t}
\t\t\t\txp_n = pread(xp_fd, xp_buf, sizeof(xp_buf) - 1, XP_MARK_OFF);
\t\t\t\tclose(xp_fd);
\t\t\t\tif (xp_n < (ssize_t)(sizeof(xp_mark) - 1) ||
\t\t\t\t    memcmp(xp_buf, xp_mark, sizeof(xp_mark) - 1) != 0) {
\t\t\t\t\tprintf("  xpcproxy: %s: no %s at 0x%X: not a patched xpcproxy\\n",
\t\t\t\t\t    xp_file, xp_mark, (unsigned)XP_MARK_OFF);
\t\t\t\t\tcontinue;
\t\t\t\t}
\t\t\t\txp_buf[xp_n] = 0;
\t\t\t\t{
\t\t\t\t\tchar *xp_nl = strchr(xp_buf, '\\n');

\t\t\t\t\tif (xp_nl != NULL) {
\t\t\t\t\t\t*xp_nl = 0;
\t\t\t\t\t}
\t\t\t\t}
\t\t\t\tprintf("  xpcproxy: payload: %s: \\"%s\\"\\n", xp_file, xp_buf);
\t\t\t\txp_from = xp_dir;
\t\t\t\tbreak;
\t\t\t}
\t\t}

\t\tif (xp_from == NULL) {
\t\t\tprintf("  xpcproxy: no patched copy in the payload (patch/libexec/, usr/libexec/): "
\t\t\t    "/usr/libexec/xpcproxy stays Darwin's\\n");
\t\t} else {
\t\t\txp_m = mount("bindfs", "/usr/libexec", MNT_RDONLY | MNT_UNION,
\t\t\t    (void *)xp_from);
\t\t\tif (xp_m != 0) {
\t\t\t\tprintf("  xpcproxy: bindfs %s on /usr/libexec, union: %s\\n",
\t\t\t\t    xp_from, strerror(errno));
\t\t\t} else {
\t\t\t\tprintf("  xpcproxy: bindfs %s on /usr/libexec, union: ok\\n", xp_from);
\t\t\t}
\t\t}

\t\tif (xp_m == 0) {
\t\t\t/* (1) the file launchd will exec is the patched one ... */
\t\t\txp_fd = open("/usr/libexec/xpcproxy", O_RDONLY, 0);
\t\t\tif (xp_fd >= 0) {
\t\t\t\txp_n = pread(xp_fd, xp_buf, sizeof(xp_mark) - 1, XP_MARK_OFF);
\t\t\t\tclose(xp_fd);
\t\t\t\txp_ours = xp_n == (ssize_t)(sizeof(xp_mark) - 1) &&
\t\t\t\t    memcmp(xp_buf, xp_mark, sizeof(xp_mark) - 1) == 0;
\t\t\t}
\t\t\t/* ... (2) and the volume's other programs are still there */
\t\t\txp_whole = stat("/usr/libexec/launchd_cache_loader", &xp_st) == 0;
\t\t\tprintf("  xpcproxy: /usr/libexec/xpcproxy: %s\\n",
\t\t\t    xp_ours ? "the patched one" : "NOT the patched one");
\t\t\tprintf("  xpcproxy: /usr/libexec/launchd_cache_loader: %s\\n",
\t\t\t    xp_whole ? "still there, the union falls through"
\t\t\t    : "MISSING, the union is not falling through");
\t\t\tif (xp_ours && xp_whole) {
\t\t\t\txp_ok = 1;
\t\t\t} else if (mini_syscall(XP_UNMOUNT, (long)(void *)"/usr/libexec", 0, 0, 0, 0, 0) == 0) {
\t\t\t\tprintf("  xpcproxy: unmounted: /usr/libexec is the volume's own again\\n");
\t\t\t} else {
\t\t\t\tprintf("  xpcproxy: unmount /usr/libexec: %s\\n", strerror(errno));
\t\t\t}
\t\t}
\t\tcheck(xp_ok, "xpcproxy: the patched one over Darwin's (getpwnam skipped)");
\t}
'''


def find_init_c(where):
    """init.c under a userland dir, a tree root (hakios/), or given as the file itself."""
    base = pathlib.Path(where)
    for rel in ("userland/src/init.c", "src/init.c", "init.c", "userland/init.c"):
        f = base / rel
        if f.is_file():
            return f
    if base.is_file():
        return base
    return None


def main(where):
    f = find_init_c(where)
    if f is None:
        print("patch-init-xpcproxy: init.c not found (looked under %s)" % where)
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-xpcproxy: already applied (%s)" % f)
        return 0

    n_flash = text.count(FLASH_ROOT_ANCHOR)
    n_switch = text.count(SWITCH_ROOT_ANCHOR)
    n_anchor = text.count(ANCHOR)
    if n_flash != 1:
        print("patch-init-xpcproxy: MISS: flash_root() is not there once (%d times)" % n_flash)
        return 1
    if n_switch != 1:
        print("patch-init-xpcproxy: MISS: the switch_root sysctl call is not there once (%d times)"
              % n_switch)
        return 1
    if n_anchor != 1:
        print("patch-init-xpcproxy: MISS: flash_root_mode = 1 appears %d times%s" % (
            n_anchor, " (is this the expected init.c, with LF line endings?)" if not n_anchor else ""))
        return 1
    i_flash = text.index(FLASH_ROOT_ANCHOR)
    i_switch = text.index(SWITCH_ROOT_ANCHOR)
    i_anchor = text.index(ANCHOR)
    if not i_flash < i_switch < i_anchor:
        print("patch-init-xpcproxy: MISS: the end of flash_root() is not after its switch_root "
              "call (flash_root %d, switch_root %d, end %d)" % (i_flash, i_switch, i_anchor))
        return 1

    text = text.replace(ANCHOR, BLOCK + ANCHOR, 1)
    f.write_text(text, errors="surrogateescape")

    line = text[:i_anchor].count("\n") + 1
    print("patch-init-xpcproxy: the patched xpcproxy bound over Darwin's at the end of flash_root")
    print("patch-init-xpcproxy: %s line %d (after the switch root, after /redwood is bound)"
          % (f, line))
    print("patch-init-xpcproxy: payload /redwood/patch/libexec/xpcproxy (MARK HAKIOS-XPCPROXY-PATCH "
          "at 0x8558), else /redwood/usr/libexec/xpcproxy")
    print("patch-init-xpcproxy: boot log: grep -a 'xpcproxy:' and '[ ok ] xpcproxy:'")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-xpcproxy.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
