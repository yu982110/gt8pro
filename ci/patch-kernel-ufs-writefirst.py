#!/usr/bin/env python3
"""HAKIOS-LAFA-UFS-WRITEFIRST: the data volume starts at the darwin partition, not 8 GiB into it.

The loader presents its writable data volume window at DARWIN_FIRST_BLOCK (the start of the
darwin partition: xnuloader/loader.c block_start() sets
`start_lba = userdata.start_lba + DARWIN_FIRST_BLOCK`).  The kernel's own driver instead starts
the device UFS_WRITE_FIRST blocks (2042368 = 8.17 GiB) into that partition, so in native mode
/dev/ufs_darwin_data reads past the HFS+ volume header the loader window sees:

    HFS Plus from /dev/ufs_darwin_data: Invalid argument; a memory filesystem instead

and /private/var falls back to a memory filesystem, which makes launchd's restore-datapartition
boot task fail (the boot stops there).

The HFS+ image on the phone sits at the partition start (verified: 'H+' at offset 0x400 of
userdata block 79500000).  Move the window to the partition start so both paths agree.

Idempotent.

    python3 ci/patch-kernel-ufs-writefirst.py <xnu tree>
"""
import pathlib
import re
import sys

MARK = "HAKIOS-LAFA-UFS-WRITEFIRST"
PAT = re.compile(r"(?m)^#define\s+UFS_WRITE_FIRST\s+[0-9]+ULL.*$")
NEW = "#define UFS_WRITE_FIRST    0ULL   /* " + MARK + ": the data volume starts at the partition */"


def find_driver(root: pathlib.Path):
    # root = <repo>/hakios/xnu-src/xnu-8796.121.2, so the driver source is two levels up under
    # hakios/drivers/storage/.  Never fall back to the xnu tree: a stale synced copy there is
    # overwritten by make-kernel.sh and patching it does nothing.
    for cand in (
        root.parent.parent / "drivers/storage/hakios.ufs.c",
        root.parent.parent.parent / "hakios/drivers/storage/hakios.ufs.c",
    ):
        if cand.exists():
            return cand
    return None


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    f = find_driver(root)
    if f is None:
        print("patch-ufs-writefirst: MISS hakios.ufs.c")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-ufs-writefirst: already applied")
        return 0
    m = PAT.search(text)
    if m is None:
        print("patch-ufs-writefirst: MISS UFS_WRITE_FIRST")
        return 1
    print("patch-ufs-writefirst: found: %s" % m.group(0).strip())
    text = text[:m.start()] + NEW + text[m.end():]
    f.write_text(text, errors="surrogateescape")
    print("patch-ufs-writefirst: data volume window moved to the darwin partition start")
    return 0


if __name__ == "__main__":
    sys.exit(main())
