#!/usr/bin/env python3
"""HAKIOS-LAFA-UFS-WRITEFIRST: the data volume starts at the darwin partition, not 8 GiB into it.

The loader presents its writable data volume window at DARWIN_FIRST_BLOCK (the start of the
darwin partition: xnuloader/loader.c block_start() sets
`start_lba = userdata.start_lba + DARWIN_FIRST_BLOCK`).  The kernel's own driver instead starts
the device UFS_WRITE_FIRST = 2042368 blocks (8.17 GiB) into that partition:

    #define UFS_WRITE_FIRST    2042368ULL   /* darwin's blocks of 4096 */

so in native mode /dev/ufs_darwin_data reads 8.17 GiB past the HFS+ volume header the loader
window sees -- HFS+ then fails to mount with EINVAL ("HFS Plus from /dev/ufs_darwin_data: Invalid
argument; a memory filesystem instead"), /private/var is a memory filesystem, and launchd's
restore-datapartition boot task fails, which is where the boot stops.

The HFS+ image on the phone sits at the partition start (verified: 'H+' at offset 0x400 of
userdata block 79500000).  Move the window to the partition start so both paths agree.

Idempotent.

    python3 ci/patch-kernel-ufs-writefirst.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-UFS-WRITEFIRST"
OLD = "#define UFS_WRITE_FIRST    2042368ULL"
NEW = "#define UFS_WRITE_FIRST    0ULL   /* %s: the data volume starts at the partition */" % MARK


def find_driver(root: pathlib.Path):
    for cand in (
        root.parent.parent.parent / "drivers/storage/hakios.ufs.c",
        root.parent.parent / "drivers/storage/hakios.ufs.c",
    ):
        if cand.exists():
            return cand
    return next(root.rglob("hakios.ufs.c"), None)


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
    if OLD not in text:
        print("patch-ufs-writefirst: MISS UFS_WRITE_FIRST")
        return 1
    text = text.replace(OLD, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-ufs-writefirst: data volume window moved to the darwin partition start")
    return 0


if __name__ == "__main__":
    sys.exit(main())
