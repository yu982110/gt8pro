#!/usr/bin/env python3
"""patch-init-exec-launchd.py -- hand process 1 to the real launchd after the mounts.

flash_root()+mount_writable() have run, so /private/var is the HFS data volume and jobbind has put
the payload back on /redwood. The payload's own /sbin/launchd.darwin is the patched one (logging
kept on the console, RAM-disk mode not set), so that is the copy to exec; the Darwin volume's own
/sbin/launchd.darwin is the fallback.

The block now says enough to tell the three outcomes apart on a photograph, because "launchd did not
run" has three different causes and the console used to be silent about all of them:

    HAKIOS: exec launchd now: /redwood/sbin/launchd.darwin     <- init got here and is handing over
    (nothing after that)                                        -> execve took over: launchd is running
                                                                   (or it panicked inside launchd)
    HAKIOS: exec launchd FAILED: <path>: <strerror>             -> execve came back: the reason is here
    HAKIOS: exec launchd: <path>: <strerror>                    -> not even stat()able
    HAKIOS: exec launchd: no root_apfs, not handing over        -> the command line never reached init
    HAKIOS: exec launchd: nothing to hand over to               -> no candidate existed

A successful execve never returns, so the 30 s busy wait at the end of the block is only ever
reached when the hand-over did not happen -- which is exactly when the line has to be photographed.

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
\t *
\t * execve that works never returns: every line printed after it is a failure, and the wait after
\t * the block holds such a line on the screen long enough to photograph.
\t *
\t * configure_inject() must run here and not after main()'s checks: the hand-over is what follows
\t * mount_writable(), so the call further down main() is never reached, and without it
\t * kern.exec_inject keeps an empty table -- launchd then starts every daemon without the port's
\t * libhakios, which is why nothing painted the panel.
\t */
\tconfigure_inject();
\t{
\t\tstatic const char *const hk_ld[] = {
\t\t\t"/redwood/sbin/launchd.darwin", "/sbin/launchd.darwin",
\t\t};
\t\tint hk_called = 0;
\t\tstruct stat hk_st;

\t\tif (boot_arg("root_apfs") != NULL) {
\t\t\tfor (unsigned hk_i = 0; hk_i < sizeof(hk_ld) / sizeof(hk_ld[0]); hk_i++) {
\t\t\t\tif (stat(hk_ld[hk_i], &hk_st) != 0) {
\t\t\t\t\tprintf("HAKIOS: exec launchd: %s: %s\\n", hk_ld[hk_i], strerror(errno));
\t\t\t\t\tcontinue;
\t\t\t\t}
\t\t\t\tprintf("HAKIOS: exec launchd now: %s\\n", hk_ld[hk_i]);
\t\t\t\texecve(hk_ld[hk_i], (char *const[]){ (char *)hk_ld[hk_i], NULL }, envp);
\t\t\t\thk_called = 1;
\t\t\t\tprintf("HAKIOS: exec launchd FAILED: %s: %s\\n", hk_ld[hk_i], strerror(errno));
\t\t\t}
\t\t} else {
\t\t\tprintf("HAKIOS: exec launchd: no root_apfs, not handing over\\n");
\t\t}
\t\tif (!hk_called) {
\t\t\tprintf("HAKIOS: exec launchd: nothing to hand over to\\n");
\t\t}
\t\t/* about thirty seconds, in twenty holds: only reached when execve did not take over */
\t\tfor (int hk_p = 0; hk_p < 20; hk_p++) {
\t\t\tfor (volatile unsigned long hk_w = 0; hk_w < 200000000UL; hk_w++) {
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
    print("patch-init-exec-launchd: hands over after the mounts, and says why when it cannot")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-exec-launchd.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
