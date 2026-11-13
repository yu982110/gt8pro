#!/usr/bin/env python3
"""patch-init-redwoodmount.py -- put this image back on /redwood after the switch root.

flash_root ends with bind_dir("/private/preboot/redwood", "/redwood") and that fails here: the
payload volume this init runs from (the ramdisk's first volume) was not carried over when the old
root moved to /private/preboot, so /redwood/etc/rc cannot be read and init never hands over to
launchd. Instead of the bind, mount that volume again from the ramdisk it lives on (/dev/md0),
with the bind kept as a fallback.

    python3 ci/patch-init-redwoodmount.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-REDWOOD-REMOUNT"

ANCHOR = '\tcheck(bind_dir("/private/preboot/redwood", "/redwood"), "this image back on /redwood");\n'

NEW = '''\t/*
\t * HAKIOS-REDWOOD-REMOUNT: this image's payload is the ramdisk's first volume, and the switch
\t * root did not carry its mount over, so mount it again (the bind is kept as a fallback).
\t */
\t{
\t\tint hk_rw = mount("apfs", "/redwood", MNT_RDONLY, "/dev/md0,v=0");

\t\tif (hk_rw != 0) {
\t\t\thk_rw = mount("apfs", "/redwood", MNT_RDONLY, "/dev/md0");
\t\t}
\t\tcheck(hk_rw == 0 || bind_dir("/private/preboot/redwood", "/redwood"),
\t\t    "this image back on /redwood");
\t}
'''


def main(root):
    for rel in ("src/init.c", "init.c"):
        f = pathlib.Path(root) / rel
        if f.exists():
            break
    else:
        print("patch-init-redwoodmount: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-redwoodmount: already applied")
        return 0
    if text.count(ANCHOR) != 1:
        print("patch-init-redwoodmount: MISS: the bind line appears %d times" % text.count(ANCHOR))
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-redwoodmount: mounts the payload volume again after the switch root")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-redwoodmount.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
