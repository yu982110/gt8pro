#!/usr/bin/env python3
"""HAKIOS-LAFA-EXTENT-ROOT: build the FUXI extent-root path into the PANDORA kernel.

The flash root image (the 3.4 GB APFS container at 300 GiB inside userdata) can only be read by
the kernel's own UFS driver through the extent table in /chosen:

    fuxi-root-extents = array of {file block, partition block, blocks} (4 KiB units)
    fuxi-root-blocks  = the root file's size in blocks

That code exists in bsd/dev/sweet_ufs.c but is compiled only for FUXI, and its one caller is
guarded with `#if defined(FUXI) && !defined(PANDORA)` -- so a PANDORA build (ours) never loads the
extents, and every read of /dev/ufsroot0 fails with EIO.  0122 already made the table itself
PANDORA-capable (16384 entries, binary search), so only two things are missing:

  1. -DFUXI=1 in the arm64 machine flags (so the code is compiled at all);
  2. the caller's guard relaxed from `FUXI && !PANDORA` to `FUXI`.

The loader side (xnuloader/loader.c) writes the two /chosen properties from the staged extent
table.

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
    if MARK in text:
        print("patch-extent-root: define already there")
        return 0
    count = 0

    def add(m):
        nonlocal count
        line = m.group(0)
        if DEFINE in line:
            return line
        count += 1
        return line.rstrip() + " " + DEFINE + "   /* " + MARK + " */"

    text = re.sub(r"(?m)^CFLAGS_ARM64\s*=.*$", add, text)
    f.write_text(text, errors="surrogateescape")
    print("patch-extent-root: %s added to CFLAGS_ARM64 (%d line(s))" % (DEFINE, count))
    return count


def relax_guard(root: pathlib.Path) -> int:
    f = next(root.rglob("sweet_ufs.c"), None)
    if f is None:
        print("patch-extent-root: MISS sweet_ufs.c")
        return 0
    text = f.read_text(errors="surrogateescape")
    old = "#if defined(FUXI) && !defined(PANDORA)"
    if old not in text:
        print("patch-extent-root: guard already relaxed or absent")
        return 0
    text = text.replace(old, "#if defined(FUXI)   /* " + MARK + ": PANDORA too */")
    f.write_text(text, errors="surrogateescape")
    print("patch-extent-root: extent-root guard relaxed for PANDORA")
    return 1


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    a = add_define(root)
    b = relax_guard(root)
    return 0 if (a or b) else 1


if __name__ == "__main__":
    sys.exit(main())
