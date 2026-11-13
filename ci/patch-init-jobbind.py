#!/usr/bin/env python3
"""patch-init-jobbind.py -- put our launchd job files in front of Darwin's, after the switch root.

The check

    check(bind_dir(in_image("/LaunchDaemons"), "/flash/System/Library/LaunchDaemons") &&
        bind_dir(in_image("/empty"), "/flash/System/Library/NanoLaunchDaemons") &&
        bind_dir(in_image("/empty"), "/flash/System/Library/LaunchAngels") &&
        bind_dir(in_image("/xpc"), "/flash/System/Library/xpc"), "our launchd jobs, not Darwin's");

fails on this image ("[FAIL] our launchd jobs, not Darwin's"), and the two reasons are in the
target paths, not in the sources:

  * the image's /System/Library/LaunchDaemons is a symlink to /redwood/LaunchDaemons, and at that
    point /redwood is the payload itself (the RAM disk stage0 mounted on it): mounting on that
    path aims at the source's own directory, and after the switch root the link leads into the
    image's own empty /redwood -- not into our jobs;
  * /System/Library/NanoLaunchDaemons and /System/Library/LaunchAngels are not in the image at
    all (build.sh removes them), so there is nothing there to mount on.

What this patch does:

  1. replaces that block with the stat diagnostics of every source and target (base, the four
     payload directories, the four image paths and where the symlink goes), so a boot says which
     path is missing;
  2. adds, at the end of flash_root (after the switch root, before the hand-over to launchd),
     the bind itself: the payload is mounted on /redwood again from its RAM disk (/dev/md1),
     the path the image's symlink leads to, so /System/Library/LaunchDaemons finds our 25 plists;
     our xpc is bound over Darwin's (it carries the extension plists, not the 530-job cache);
     and the two missing directories are made with kern.apfs_synthetic (as flash_root already
     does for /redwood) and bound empty. A check with the old label reports the result.

Why after the switch root and not before it: a mount made on the old root's payload paths does
not survive the switch; mounting the payload on /redwood is what makes the image's own symlink
land on it; and kern.apfs_synthetic needs the Darwin volume mounted, which happens in flash_root.
Why not copy the plists into /private/var: at this point /private/var is still the read-only
image's directory -- mount_writable() has not run yet.

    python3 ci/patch-init-jobbind.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-JOBBIND"

# The four binds, in the two forms they have: with "/flash" (before patch-init-flashroot.py) and
# with "/private/flash" (after it). The patch works either side of that one: if it runs first its
# own "/flash" paths are rewritten by flashroot with the rest.
BIND_ANCHOR = """\tcheck(bind_dir(in_image("/LaunchDaemons"), "{F}/System/Library/LaunchDaemons") &&
\t    bind_dir(in_image("/empty"), "{F}/System/Library/NanoLaunchDaemons") &&
\t    bind_dir(in_image("/empty"), "{F}/System/Library/LaunchAngels") &&
\t    bind_dir(in_image("/xpc"), "{F}/System/Library/xpc"), "our launchd jobs, not Darwin's");
"""

DIAG = """\t/*
\t * HAKIOS-JOBBIND: why the four job binds are not made here any more -- printed so the log
\t * says which path is missing:
\t *   - /System/Library/LaunchDaemons in the image is a symlink to /redwood/LaunchDaemons,
\t *     and /redwood here is the payload itself (the RAM disk stage0 mounted): a bind on
\t *     that path aims at its own source, and after the switch root the link leads into the
\t *     image's own empty /redwood rather than into our jobs;
\t *   - NanoLaunchDaemons and LaunchAngels are not in the image (build.sh removes them), so
\t *     there is nothing there to mount on.
\t * They are made after the switch root, with the payload mounted on /redwood again (the
\t * path the image's symlink leads to): the block at the end of flash_root.
\t */
\t{
\t\tstatic const char *const jb_src[] = {
\t\t\t"/LaunchDaemons", "/empty", "/empty", "/xpc",
\t\t};
\t\tstatic const char *const jb_dst[] = {
\t\t\t"{F}/System/Library/LaunchDaemons",
\t\t\t"{F}/System/Library/NanoLaunchDaemons",
\t\t\t"{F}/System/Library/LaunchAngels",
\t\t\t"{F}/System/Library/xpc",
\t\t};
\t\tchar jb_path[256], jb_link[256];
\t\tstruct stat jb_st;
\t\tssize_t jb_n;
\t\tunsigned jb_i;

\t\tprintf("  jobbind: payload base '%s'; the job binds and their paths now:\\n", base);
\t\tfor (jb_i = 0; jb_i < sizeof(jb_src) / sizeof(jb_src[0]); jb_i++) {
\t\t\tstrcpy(jb_path, in_image(jb_src[jb_i]));
\t\t\tif (stat(jb_path, &jb_st) == 0) {
\t\t\t\tprintf("  jobbind: source %s -> there\\n", jb_path);
\t\t\t} else {
\t\t\t\tprintf("  jobbind: source %s -> %s\\n", jb_path, strerror(errno));
\t\t\t}
\t\t\tif (lstat(jb_dst[jb_i], &jb_st) == 0) {
\t\t\t\tprintf("  jobbind: target %s -> %s\\n", jb_dst[jb_i],
\t\t\t\t    (jb_st.st_mode & S_IFMT) == S_IFDIR ? "dir" :
\t\t\t\t    (jb_st.st_mode & S_IFMT) == S_IFLNK ? "symlink" :
\t\t\t\t    (jb_st.st_mode & S_IFMT) == S_IFREG ? "file" : "other");
\t\t\t\tif ((jb_st.st_mode & S_IFMT) == S_IFLNK) {
\t\t\t\t\tjb_n = readlink(jb_dst[jb_i], jb_link, sizeof(jb_link) - 1);
\t\t\t\t\tjb_link[jb_n > 0 ? jb_n : 0] = 0;
\t\t\t\t\tif (jb_n > 0) {
\t\t\t\t\t\tprintf("  jobbind:        -> %s\\n", jb_link);
\t\t\t\t\t}
\t\t\t\t}
\t\t\t} else {
\t\t\t\tprintf("  jobbind: target %s -> %s (nothing to mount on)\\n",
\t\t\t\t    jb_dst[jb_i], strerror(errno));
\t\t\t}
\t\t}
\t}
"""

# job_bind_path() goes before flash_root(), where bind_dir() and synthetic_dir() are defined.
FLASH_ROOT_ANCHOR = "static void\nflash_root(const char *dev)\n{\n"

HELPER = """/*
 * HAKIOS-JOBBIND: a directory for a mount point, made when the image does not have it (with
 * kern.apfs_synthetic, as flash_root makes /redwood), then FROM mounted on it. 1 when bound.
 */
static int
job_bind_path(const char *from, const char *target)
{
\tstruct stat st;

\tif (lstat(target, &st) != 0 && !synthetic_dir(target)) {
\t\tprintf("  jobbind: no directory for %s: %s\\n", target, strerror(errno));
\t\treturn 0;
\t}
\treturn bind_dir(from, target);
}

"""

REPORT_ANCHOR = "\tflash_root_mode = 1;\n"

REPORT = """\t/*
\t * HAKIOS-JOBBIND: the payload back on /redwood, then our launchd job files where launchd
\t * will read them (after the switch root, before main hands over).
\t *
\t * The payload is the RAM disk stage0 mounted on /redwood (/dev/md1). Mounting it again
\t * here, on the path the image's /System/Library/LaunchDaemons symlink leads to, is what
\t * puts our plists in front of Darwin's; the apfs driver takes a second mount of a device
\t * it already holds (apfs_vfsops.c, "Mounted already -- by us").
\t */
\t{
\t\tstatic const char *const jb_empty[] = {
\t\t\t"/System/Library/NanoLaunchDaemons", "/System/Library/LaunchAngels",
\t\t};
\t\tchar jb_payload[256], jb_from[300];
\t\tstruct stat jb_st;
\t\tunsigned jb_i;
\t\tint jb_m, jb_ours, jb_xpc;

\t\tprintf("\\n-- jobbind: launchd's job files --\\n");
\t\tif (stat("/redwood/LaunchDaemons", &jb_st) == 0) {
\t\t\tprintf("  jobbind: /redwood is the payload already\\n");
\t\t} else {
\t\t\tjb_m = mount("apfs", "/redwood", MNT_RDONLY, (void *)"/dev/md1");
\t\t\tprintf("  jobbind: /dev/md1 on /redwood: %s\\n",
\t\t\t    jb_m == 0 ? "ok" : strerror(errno));
\t\t\tif (jb_m != 0 && bind_dir("/private/preboot/redwood", "/redwood")) {
\t\t\t\tprintf("  jobbind: /private/preboot/redwood on /redwood\\n");
\t\t\t}
\t\t}
\t\tif (stat("/redwood/LaunchDaemons", &jb_st) == 0) {
\t\t\tstrcpy(jb_payload, "/redwood");
\t\t} else {
\t\t\tstrcpy(jb_payload, "/private/preboot/redwood");
\t\t\tprintf("  jobbind: no payload on /redwood; using %s\\n", jb_payload);
\t\t}
\t\t/*
\t\t * /System/Library/LaunchDaemons: the image's symlink to /redwood/LaunchDaemons lands
\t\t * on our plists with the payload back on /redwood. If it could not be put back, make
\t\t * the link's target on the volume and bind the old payload copy there.
\t\t */
\t\tif (stat("/System/Library/LaunchDaemons/com.hakios.demo.plist", &jb_st) != 0) {
\t\t\tstrcpy(jb_from, jb_payload);
\t\t\tstrcpy(jb_from + strlen(jb_from), "/LaunchDaemons");
\t\t\tif (stat(jb_from, &jb_st) == 0 &&
\t\t\t    job_bind_path(jb_from, "/redwood/LaunchDaemons")) {
\t\t\t\tprintf("  jobbind: %s on /redwood/LaunchDaemons\\n", jb_from);
\t\t\t}
\t\t}
\t\tjb_ours = stat("/System/Library/LaunchDaemons/com.hakios.demo.plist", &jb_st) == 0;
\t\tif (jb_ours) {
\t\t\tprintf("  jobbind: /System/Library/LaunchDaemons -> our plists\\n");
\t\t} else {
\t\t\tprintf("  jobbind: /System/Library/LaunchDaemons -> %s\\n", strerror(errno));
\t\t}
\t\t/*
\t\t * xpc: the image's is Darwin's (launchd.plist, the 530-job cache); ours carries the
\t\t * extension plists, which launchd then reads in place of that cache.
\t\t */
\t\tstrcpy(jb_from, jb_payload);
\t\tstrcpy(jb_from + strlen(jb_from), "/xpc");
\t\tif (stat(jb_from, &jb_st) != 0) {
\t\t\tprintf("  jobbind: no %s: %s\\n", jb_from, strerror(errno));
\t\t} else if (bind_dir(jb_from, "/System/Library/xpc")) {
\t\t\tprintf("  jobbind: %s on /System/Library/xpc\\n", jb_from);
\t\t}
\t\t/*
\t\t * The two directories the image does not have: made empty, so no Darwin job is found
\t\t * in them. kern.apfs_synthetic is what makes them mount points on a read-only volume.
\t\t */
\t\tfor (jb_i = 0; jb_i < sizeof(jb_empty) / sizeof(jb_empty[0]); jb_i++) {
\t\t\tstrcpy(jb_from, jb_payload);
\t\t\tstrcpy(jb_from + strlen(jb_from), "/empty");
\t\t\tif (stat(jb_from, &jb_st) == 0 && job_bind_path(jb_from, jb_empty[jb_i])) {
\t\t\t\tprintf("  jobbind: %s on %s\\n", jb_from, jb_empty[jb_i]);
\t\t\t}
\t\t}
\t\t/* what launchd will read */
\t\tjb_xpc = stat("/System/Library/xpc/extensions.plist", &jb_st) == 0;
\t\tprintf("  jobbind: /System/Library/xpc/extensions.plist: %s\\n", jb_xpc ? "ours" : "missing");
\t\tprintf("  jobbind: /System/Library/xpc/launchd.plist: %s\\n",
\t\t    stat("/System/Library/xpc/launchd.plist", &jb_st) == 0 ? "Darwin's, still there" : "hidden");
\t\tcheck(jb_ours && jb_xpc, "our launchd jobs, not Darwin's");
\t}
\tflash_root_mode = 1;
"""


def main(root):
    for rel in ("src/init.c", "init.c"):
        f = pathlib.Path(root) / rel
        if f.exists():
            break
    else:
        print("patch-init-jobbind: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-jobbind: already applied")
        return 0

    # The bind block, in whichever form the flash paths are in by now.
    anchor = None
    flash = None
    for prefix in ("/private/flash", "/flash"):
        candidate = BIND_ANCHOR.replace("{F}", prefix)
        if text.count(candidate) == 1:
            anchor = candidate
            flash = prefix
            break
    if anchor is None:
        print("patch-init-jobbind: MISS: the launchd job-bind check (with /flash or "
              "/private/flash) is not there once")
        return 1
    if text.count(FLASH_ROOT_ANCHOR) != 1:
        print("patch-init-jobbind: MISS: flash_root() is not where expected (%d times)"
              % text.count(FLASH_ROOT_ANCHOR))
        return 1
    if text.count(REPORT_ANCHOR) != 1:
        print("patch-init-jobbind: MISS: flash_root_mode = 1 appears %d times"
              % text.count(REPORT_ANCHOR))
        return 1

    diag = DIAG.replace("{F}", flash)
    text = text.replace(anchor, diag, 1)
    text = text.replace(FLASH_ROOT_ANCHOR, HELPER + FLASH_ROOT_ANCHOR, 1)
    text = text.replace(REPORT_ANCHOR, REPORT, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-jobbind: the payload back on /redwood, our plists at the hand-over")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-jobbind.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
