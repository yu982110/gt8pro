#!/usr/bin/env python3
"""patch-init-final.py -- say what /redwood holds right before the hand-over to launchd.

The switch root and the remount of the payload volume end with /redwood empty (no etc/rc), while
the image itself, read with apfs-tool, has /etc/rc 88 bytes in its redwood volume. This lists the
directory and its entries right before run_rc, so one boot says which volume was mounted, if any.

    python3 ci/patch-init-final.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-FINAL-LIST"

ANCHOR = '\trun_rc(in_image("/etc/rc"));\n'

NEW = '''\t/* HAKIOS-FINAL-LIST: what the switch root and the remount left on /redwood. */
\t{
\t\tstatic const char *const hk_probe[] = {
\t\t\t"/redwood", "/redwood/etc", "/redwood/usr", "/redwood/sbin", "/redwood/bin",
\t\t};
\t\tstruct stat hk_lst;

\t\tprintf("\\n  final: /redwood holds:\\n");
\t\t(void)list_tree("/redwood", 1);
\t\tfor (unsigned hk_i = 0; hk_i < sizeof(hk_probe) / sizeof(hk_probe[0]); hk_i++) {
\t\t\tif (stat(hk_probe[hk_i], &hk_lst) == 0) {
\t\t\t\tprintf("  final: %s -> %lld\\n", hk_probe[hk_i], (long long)hk_lst.st_size);
\t\t\t} else {
\t\t\t\tprintf("  final: %s -> %s\\n", hk_probe[hk_i], strerror(errno));
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
        print("patch-init-final: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-final: already applied")
        return 0
    if text.count(ANCHOR) != 1:
        print("patch-init-final: MISS: the run_rc line appears %d times" % text.count(ANCHOR))
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-final: lists /redwood before the hand-over")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-final.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
