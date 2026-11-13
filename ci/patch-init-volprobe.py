#!/usr/bin/env python3
"""patch-init-volprobe.py -- find which volume of the ramdisk is this image, and mount that.

The remount of the payload volume after the switch root used /dev/md0,v=0 and left /redwood empty
(it holds no etc/rc), so init never read /redwood/etc/rc and never handed over to launchd. This
tries the ramdisk's containers and volumes in turn, keeping the one whose /redwood/etc/rc exists,
and says which it was.

    python3 ci/patch-init-volprobe.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-VOLPROBE"

START = '\t\tint hk_rw = mount("apfs", "/redwood", MNT_RDONLY, "/dev/md0,v=0");'
END = '\t\tcheck(hk_rw == 0 || bind_dir("/private/preboot/redwood", "/redwood"),'

NEW = '''\t\tstatic const char *const hk_vols[] = {
\t\t\t"/dev/md0,v=0", "/dev/md0,v=1", "/dev/md0,v=2",
\t\t\t"/dev/md0,v=3", "/dev/md0",
\t\t};
\t\tint hk_rw = -1;

\t\tfor (unsigned hk_v = 0; hk_v < sizeof(hk_vols) / sizeof(hk_vols[0]); hk_v++) {
\t\t\tstruct stat hk_rc;

\t\t\thk_rw = mount("apfs", "/redwood", MNT_RDONLY, (void *)hk_vols[hk_v]);
\t\t\tif (hk_rw != 0) {
\t\t\t\tprintf("  volprobe: %s -> %s\\n", hk_vols[hk_v], strerror(errno));
\t\t\t\tcontinue;
\t\t\t}
\t\t\tif (stat("/redwood/etc/rc", &hk_rc) != 0) {
\t\t\t\tprintf("  volprobe: %s -> mounted, no etc/rc\\n", hk_vols[hk_v]);
\t\t\t\thk_rw = -1;
\t\t\t\tcontinue;
\t\t\t}
\t\t\tprintf("  volprobe: %s -> ok (%lld bytes of rc)\\n", hk_vols[hk_v],
\t\t\t    (long long)hk_rc.st_size);
\t\t\tbreak;
\t\t}
'''


def main(root):
    for rel in ("src/init.c", "init.c"):
        f = pathlib.Path(root) / rel
        if f.exists():
            break
    else:
        print("patch-init-volprobe: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-volprobe: already applied")
        return 0
    i = text.find(START)
    j = text.find(END, i)
    if i < 0 or j < 0:
        print("patch-init-volprobe: MISS: start %d end %d" % (i, j))
        return 1
    text = text[:i] + NEW + text[j:]
    f.write_text(text, errors="surrogateescape")
    print("patch-init-volprobe: tries the ramdisk's volumes for this image")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-volprobe.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
