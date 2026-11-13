#!/usr/bin/env python3
"""patch-init-exec-launchd.py -- hand process 1 to the real launchd after the mounts.

flash_root()+mount_writable() have run, so /private/var is the HFS data volume and jobbind has put
the payload back on /redwood. The payload's own /sbin/launchd.darwin is the patched one (logging
kept on the console, RAM-disk mode not set), so that is the copy to exec; the Darwin volume's own
/sbin/launchd.darwin is the fallback.

    python3 ci/patch-init-exec-launchd.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-EXEC-LAUNCHD"

ANCHOR = "\tmount_writable();\n"

ADD = '''\t/*
\t * HAKIOS-EXEC-LAUNCHD: the switch root has happened and the data volume is mounted. Hand over
\t * to the payload's launchd.darwin (patched: NOP at 0x23EC0 keeps its log on the console, NOP at
\t * 0x41408 leaves the RAM-disk mode unset), falling back to the volume's own copy.
\t */
\t{
\t\tstatic const char *const hk_ld[] = {
\t\t\t"/redwood/sbin/launchd.darwin", "/sbin/launchd.darwin",
\t\t};
\t\tstruct stat hk_st;

\t\tif (boot_arg("root_apfs") != NULL) {
\t\t\tfor (unsigned hk_i = 0; hk_i < sizeof(hk_ld) / sizeof(hk_ld[0]); hk_i++) {
\t\t\t\tif (stat(hk_ld[hk_i], &hk_st) != 0) {
\t\t\t\t\tprintf("  hand over: %s: %s\\n", hk_ld[hk_i], strerror(errno));
\t\t\t\t\tcontinue;
\t\t\t\t}
\t\t\t\tprintf("\\n=== hand over to %s ===\\n", hk_ld[hk_i]);
\t\t\t\texecve(hk_ld[hk_i], (char *const[]){ (char *)hk_ld[hk_i], NULL }, envp);
\t\t\t\tprintf("  execve: %s\\n", strerror(errno));
\t\t\t}
\t\t}
\t}
'''


def main(root):
    for rel in ("src/init.c", "init.c"):
        f = pathlib.Path(root) / rel
        if f.exists():
            break
    else:
        print("patch-init-exec-launchd: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-exec-launchd: already applied")
        return 0
    if text.count(ANCHOR) != 1:
        print("patch-init-exec-launchd: MISS: the mount_writable call appears %d times" % text.count(ANCHOR))
        return 1
    text = text.replace(ANCHOR, ANCHOR + ADD, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-exec-launchd: hands over to the payload's launchd.darwin after the mounts")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-exec-launchd.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
