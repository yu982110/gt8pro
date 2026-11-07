#!/usr/bin/env python3
"""patch-kernel-4k-pages.py -- make the kernel use 4 KiB pages, which is what this SoC has.

Three facts, in order:

1. The loader prints the memory model register at boot:
       MMFR0 = 0x2100122100001024
         TGran4  (bits 19:16) = 2  -> 4 KiB translation granules supported
         TGran16 (bits 27:24) = 0  -> 16 KiB translation granules NOT supported

2. The page size is not decided at compile time. osfmk/arm/arm_init.c picks it from how much
   memory the kernel was handed:

       if (args->memSize > 1 GB)
               PAGE_SHIFT_CONST = PAGE_MAX_SHIFT;   /* 16 KiB */
       else
               PAGE_SHIFT_CONST = ARM_PGSHIFT;      /* the hardware granule */

   Our window is 6.5 GB, so the kernel took the first branch and ran 16 KiB pages on a core that
   cannot translate them. That is where the broken memory accounting came from:

       apfs: pagein asks for 32768 bytes at a time (page size 16384)
       memorystatus_available_pages: 2473                    (40 MB free on a 6.5 GB window)
       coredump: map size: 417687453696                      (417 GB, in a launchd crash)

   Both branches now take ARM_PGSHIFT.

3. The board headers also have to say "4 KiB": they define __ARM_16K_PG__ (16 KiB granule) and,
   for a 4 KiB port, __ARM64_PMAP_SUBPAGE_L1__ instead. The same-SoC reference does exactly this:

       #if defined(PANDORA)
       /* SM8850 (Oryon) has no 16K translation granule: 4 KiB pages like BCM2837. */
       #define __ARM64_PMAP_SUBPAGE_L1__ 1
       #else
       #define __ARM_16K_PG__            1
       #endif

   We had only removed __ARM_16K_PG__; the subpage-L1 half was missing, so the pmap still assumed
   the mapping shape of a 16 KiB kernel.

    python3 ci/patch-kernel-4k-pages.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-4K"
GRANULE_MARK = "HAKIOS-LAFA-4K-GRANULE"

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

GRANULE_OLD = "#define __ARM_16K_PG__            1\n"

GRANULE_NEW = """/* HAKIOS-LAFA-4K-GRANULE: no 16 KiB granule on this SoC; 4 KiB pages, subpage L1, like BCM2837. */
#define __ARM64_PMAP_SUBPAGE_L1__ 1
"""


def patch_page_selection(root):
    f = root / "osfmk/arm/arm_init.c"
    if not f.exists():
        print(f"patch-4k: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-4k: page selection already patched")
        return 0
    if OLD not in text:
        print("patch-4k: MISS: the page size selection is not the expected shape")
        return 1
    text = text.replace(OLD, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-4k: the page size now follows the hardware granule (4 KiB on this SoC)")
    return 0


def patch_board_headers(root):
    """__ARM_16K_PG__ out, __ARM64_PMAP_SUBPAGE_L1__ in, for every arm64 board header."""
    changed = 0
    for f in sorted((root / "pexpert/pexpert/arm64").glob("*.h")):
        text = f.read_text(errors="surrogateescape")
        if GRANULE_MARK in text:
            continue
        if GRANULE_OLD not in text:
            continue
        text = text.replace(GRANULE_OLD, GRANULE_NEW, 1)
        f.write_text(text, errors="surrogateescape")
        changed += 1
        print(f"patch-4k: {f.name}: 16 KiB granule -> 4 KiB subpage L1")
    if changed == 0:
        print("patch-4k: no board header still defined __ARM_16K_PG__ (already done)")
    return 0


def main(root):
    if len(sys.argv) < 2:
        print("usage: patch-kernel-4k-pages.py <xnu tree>")
        raise SystemExit(2)
    root = pathlib.Path(sys.argv[1])
    rc = patch_page_selection(root)
    rc |= patch_board_headers(root)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
