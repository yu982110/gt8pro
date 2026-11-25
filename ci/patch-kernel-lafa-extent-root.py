#!/usr/bin/env python3
"""HAKIOS-LAFA-EXTENT-ROOT: build the FUXI extent-root path into the PANDORA kernel.

The flash root image (the 3.4 GB APFS container at 300 GiB inside userdata) can only be read by
the kernel's own UFS driver through the extent table in /chosen:

    fuxi-root-extents = array of {file block, partition block, blocks} (4 KiB units)
    fuxi-root-blocks  = the root file's size in blocks

That code exists in bsd/dev/sweet_ufs.c and is compiled only for FUXI.  The loader side
(xnuloader/loader.c) writes the two /chosen properties from the staged extent table; this patch
only turns the code on for our PANDORA build by adding -DFUXI=1 to CFLAGS_ARM64.

The define is inserted right after the "=" so a line that ends in a backslash keeps its
continuation (appending at the end of such a line breaks make with "recipe commences before first
target").

Idempotent.

    python3 ci/patch-kernel-lafa-extent-root.py <xnu tree>
"""
import pathlib
import re
import sys

MARK = "HAKIOS-LAFA-EXTENT-ROOT"
DEFINE = "-DFUXI=1"


def add_define(root: pathlib.Path) -> int:
    f = root / "makedefs/MakeInc.def"
    if not f.exists():
        print("patch-extent-root: MISS makedefs/MakeInc.def")
        return 0
    text = f.read_text(errors="surrogateescape")
    if DEFINE in text:
        print("patch-extent-root: %s already there" % DEFINE)
        return 0
    count = 0

    def add(m):
        nonlocal count
        count += 1
        return m.group(1) + DEFINE + " " + m.group(2)

    text = re.sub(r"(?m)^(CFLAGS_ARM64	= )(.*)$", add, text)
    text = re.sub(r"(?m)^(CFLAGS_ARM64 = )(.*)$", add, text)
    f.write_text(text, errors="surrogateescape")
    print("patch-extent-root: %s added to CFLAGS_ARM64 (%d line(s))" % (DEFINE, count))
    return count


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    return 0 if add_define(root) else 1


if __name__ == "__main__":
    sys.exit(main())
