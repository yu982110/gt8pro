#!/usr/bin/env python3
"""patch-kernel-darwin-min.py -- let a 1 GiB synthetic darwin partition take writes.

The kernel's ufs driver only accepts a partition called darwin for writes when it is at least
UFS_DARWIN_MIN blocks (20 GiB) and the loader's write_first equals UFS_WRITE_FIRST (the port's
layout constant). The loader here presents a 1 GiB partition out of userdata instead (the phone
has no free space for the real one), so both constants are relaxed to match: any size the loader
offered is taken, and writes start at block 0 of it.

    python3 ci/patch-kernel-darwin-min.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-DARWIN-MIN"

OLD_MIN = "#define UFS_DARWIN_MIN     5242875ULL"
NEW_MIN = "#define UFS_DARWIN_MIN     262144ULL          /* HAKIOS: 1 GiB, the loader\'s synthetic partition */"
OLD_FIRST = "#define UFS_WRITE_FIRST    2042368ULL"
NEW_FIRST = "#define UFS_WRITE_FIRST    0ULL               /* HAKIOS: writes from block 0 of that partition */"


def main(root):
    f = pathlib.Path(root) / "drivers/storage/hakios.ufs.c"
    if not f.exists():
        alts = list(pathlib.Path(root).rglob("hakios.ufs.c"))
        if alts:
            f = alts[0]
        else:
            print("patch-darwin-min: hakios.ufs.c not found")
            return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-darwin-min: already applied")
        return 0
    changed = 0
    for old, new in ((OLD_MIN, NEW_MIN), (OLD_FIRST, NEW_FIRST)):
        if text.count(old) == 1:
            text = text.replace(old, new, 1)
            changed += 1
        else:
            print("patch-darwin-min: MISS(%s)" % old.split()[1])
    if changed == 0:
        return 1
    f.write_text(text, errors="surrogateescape")
    print("patch-darwin-min: relaxed %d constant(s)" % changed)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-darwin-min.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
