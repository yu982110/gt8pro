#!/usr/bin/env python3
"""patch-kernel-4k-pages.py -- make the kernel use 4 KiB pages, which is what this SoC has.

Two facts, in order:

1. The loader prints the memory model register at boot:
       MMFR0 = 0x2100122100001024
         TGran4  (bits 19:16) = 2  -> 4 KiB translation granules supported
         TGran16 (bits 27:24) = 0  -> 16 KiB translation granules NOT supported

2. But the page size is not decided at compile time. osfmk/arm/arm_init.c picks it from how much
   memory the kernel was handed:

       if (args->memSize > 1 GB)
               PAGE_SHIFT_CONST = PAGE_MAX_SHIFT;   /* 16 KiB */
       else
               PAGE_SHIFT_CONST = ARM_PGSHIFT;      /* the hardware granule */

   Our window is 6.5 GB, so the kernel takes the first branch and runs 16 KiB pages on a core that
   cannot translate them. That is where the broken memory accounting came from:

       apfs: pagein asks for 32768 bytes at a time (page size 16384)
       memorystatus_available_pages: 2473                    (40 MB free on a 6.5 GB window)
       coredump: map size: 417687453696                      (417 GB, in a launchd crash)

   Removing __ARM_16K_PG__ from the board headers (H13.h, VMAPPLE.h) changes nothing, because the
   branch above overrides it. This patch makes both branches take ARM_PGSHIFT.

    python3 ci/patch-kernel-4k-pages.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-4K"

OLD = """	{
		/*
		 * Select the advertised kernel page size.
		 */
		if (args->memSize > 1ULL * 1024 * 1024 * 1024) {
			/*
			 * arm64 device with > 1GB of RAM:
			 * kernel uses 16KB pages.
			 */
			PAGE_SHIFT_CONST = PAGE_MAX_SHIFT;
		} else {
			/*
			 * arm64 device with <= 1GB of RAM:
			 * kernel uses hardware page size
			 * (4KB for H6/H7, 16KB for H8+).
			 */
			PAGE_SHIFT_CONST = ARM_PGSHIFT;
		}
"""

NEW = """	{
		/*
		 * Select the advertised kernel page size.
		 *
		 * HAKIOS-LAFA-4K: the "more than 1 GB of RAM" branch used to pick PAGE_MAX_SHIFT (16 KiB),
		 * but this SoC's cores cannot translate 16 KiB pages at all -- the loader's MMFR0 reads
		 * TGran16 = 0 -- so the kernel ran with a granule the hardware does not have, and its
		 * memory accounting came apart (40 MB free on a 6.5 GB window, a 417 GB crash core).
		 * Both branches now take the hardware granule, which is 4 KiB here.
		 */
		PAGE_SHIFT_CONST = ARM_PGSHIFT;
"""


def main(root):
    root = pathlib.Path(root)
    f = root / "osfmk/arm/arm_init.c"
    if not f.exists():
        print(f"patch-4k: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-4k: already applied")
        return 0
    if OLD not in text:
        print("patch-4k: MISS: the page size selection is not the expected shape")
        return 1
    text = text.replace(OLD, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-4k: the page size now follows the hardware granule (4 KiB on this SoC)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-4k-pages.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
