#!/usr/bin/env python3
"""patch-kernel-4k-pages.py -- make the kernel use 4K pages, which is what this SoC has.

The loader prints the memory model register at boot:

    MMFR0 = 0x2100122100001024
      TGran4  (bits 19:16) = 2   -> 4 KiB translation granules supported
      TGran16 (bits 27:24) = 0   -> 16 KiB translation granules NOT supported

Our kernel builds with __ARM_16K_PG__ defined (osfmk/mach/arm/vm_param.h picks PAGE_SHIFT from it,
and osfmk/arm64/proc_reg.h takes ARM_PGSHIFT from it), so it runs with 16 KiB pages on a core that
cannot translate them. That is where the broken memory accounting comes from:

    apfs: pagein asks for 32768 bytes at a time (page size 16384)
    memorystatus_available_pages: 2473                       (40 MB free, on a 6.5 GB window)
    coredump: map size: 417687453696                         (417 GB in a launchd crash)

The reference port for the same cores says the same thing: "the Oryon cores do not support 16K
pages (MMFR0 TGran16 = 0); the kernel uses 4K, based on xnu-8796's BCM2837 configuration plus
UniXNU's three 4K patches".

This removes the define from the board headers, so PAGE_SHIFT / ARM_PGSHIFT fall back to the 4K
path.

    python3 ci/patch-kernel-4k-pages.py <xnu tree>
"""
import pathlib
import re
import sys

MARK = "HAKIOS-LAFA-4K"

# The board headers each define it; the define is what has to go.
PATTERN = re.compile(r"^[ \t]*#define[ \t]+__ARM_16K_PG__[ \t]*(1)?[ \t]*\n", re.M)


def main(root):
    root = pathlib.Path(root)
    board_dir = root / "pexpert/pexpert/arm64"
    if not board_dir.is_dir():
        print(f"patch-4k: {board_dir} not found")
        return 1

    touched = []
    for f in sorted(board_dir.glob("*.h")):
        text = f.read_text(errors="surrogateescape")
        if MARK in text:
            continue
        new, n = PATTERN.subn(
            "/* " + MARK + ": this SoC has no 16 KiB pages (MMFR0 TGran16 = 0); the define is "
            "removed so PAGE_SHIFT stays 4 KiB. */\n",
            text,
        )
        if n:
            f.write_text(new, errors="surrogateescape")
            touched.append((f.name, n))

    # the same define can arrive from the command line; that is out of this script's reach, so
    # say so rather than pretending it was handled
    if not touched:
        already = any(MARK in p.read_text(errors="surrogateescape") for p in board_dir.glob("*.h"))
        print("patch-4k: already applied" if already else "patch-4k: MISS: no board header defines __ARM_16K_PG__")
        return 0 if already else 1

    for name, n in touched:
        print(f"patch-4k: {name}: {n} define(s) removed")
    print(f"patch-4k: {len(touched)} board header(s) now build 4 KiB pages")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-4k-pages.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
