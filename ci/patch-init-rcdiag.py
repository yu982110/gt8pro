#!/usr/bin/env python3
"""patch-init-rcdiag.py -- say what became of the rc file init reads to hand over to launchd.

run_rc(in_image("/etc/rc")) is where process 1 becomes Darwin's launchd, and here it prints nothing
at all, which means read_whole failed before the "=== FILE ===" header. This prints the path, the
state of the directories on the way and of the file itself, before run_rc runs.

    python3 ci/patch-init-rcdiag.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-RCDIAG"

ANCHOR = '\trun_rc(in_image("/etc/rc"));\n'

NEW = '''\t/* HAKIOS-RCDIAG: what became of the file that hands process 1 over to launchd. */
\t{
\t\tstatic const char *const hk_paths[] = {
\t\t\t"/redwood", "/redwood/etc", "/redwood/etc/rc", "/private/preboot/redwood/etc/rc",
\t\t};
\t\tstruct stat hk_st;

\t\tfor (unsigned hk_i = 0; hk_i < sizeof(hk_paths) / sizeof(hk_paths[0]); hk_i++) {
\t\t\tif (stat(hk_paths[hk_i], &hk_st) == 0) {
\t\t\t\tprintf("  rc: %s -> %lld bytes\\n", hk_paths[hk_i], (long long)hk_st.st_size);
\t\t\t} else {
\t\t\t\tprintf("  rc: %s -> %s\\n", hk_paths[hk_i], strerror(errno));
\t\t\t}
\t\t}
\t}
\trun_rc(in_image("/etc/rc"));
'''


def main(root):
    for rel in ("src/init.c", "init.c"):
        f = pathlib.Path(root) / rel
        if f.exists():
            break
    else:
        print("patch-init-rcdiag: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-rcdiag: already applied")
        return 0
    if text.count(ANCHOR) != 1:
        print("patch-init-rcdiag: MISS: the run_rc line appears %d times" % text.count(ANCHOR))
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-rcdiag: prints what the rc lookup found")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-rcdiag.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
