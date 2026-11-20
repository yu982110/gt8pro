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





# ------------------------------------------------------------------------------------------------
# The user map and the commpage have to agree with the kernel's page size.  Missing this is what
# made copyinstr() take a data abort on the first user-pointer load: the kernel translated 4 KiB
# pages while the VM map of every 64-bit process still described 16 KiB entries, so the pmap walk
# for user memory went off the rails.  This is the same change the reference's
# 0104-unixnu-native-4k-user-abi.patch makes.
MARK_USERMAP = "HAKIOS-LAFA-4K-USERMAP"

MACH_OLD = """	if (result->is_64bit_addr) {
		/* enforce 16KB alignment of VM map entries */
		vm_map_set_page_shift(map, SIXTEENK_PAGE_SHIFT);
	} else {
		vm_map_set_page_shift(map, page_shift_user32);
	}
"""

MACH_NEW = """	if (result->is_64bit_addr) {
#if !defined(APPLE_ARM64_ARCH_FAMILY) && !__ARM_16K_PG__
		/* HAKIOS-LAFA-4K-USERMAP: this kernel translates 4 KiB pages (the SoC has no 16 KiB
		 * granule), so every 64-bit process's map has to describe 4 KiB entries as well.
		 * With a 16 KiB map on 4 KiB hardware the pmap walk for user memory is wrong, and
		 * the first copyin from user space (open's path string) takes a data abort. */
		vm_map_set_page_shift(map, FOURK_PAGE_SHIFT);
#else
		/* enforce 16KB alignment of VM map entries */
		vm_map_set_page_shift(map, SIXTEENK_PAGE_SHIFT);
#endif
	} else {
		vm_map_set_page_shift(map, page_shift_user32);
	}
"""

COMM_OLD = """	*((uint8_t*)(_COMM_PAGE_USER_PAGE_SHIFT_32_LEGACY + _COMM_PAGE_RW_OFFSET)) = (uint8_t) page_shift_user32;
	*((uint8_t*)(_COMM_PAGE_USER_PAGE_SHIFT_32 + _COMM_PAGE_RO_OFFSET)) = (uint8_t) page_shift_user32;
	*((uint8_t*)(_COMM_PAGE_USER_PAGE_SHIFT_64_LEGACY + _COMM_PAGE_RW_OFFSET)) = (uint8_t) SIXTEENK_PAGE_SHIFT;
	*((uint8_t*)(_COMM_PAGE_USER_PAGE_SHIFT_64 + _COMM_PAGE_RO_OFFSET)) = (uint8_t) SIXTEENK_PAGE_SHIFT;
"""

COMM_NEW = """	*((uint8_t*)(_COMM_PAGE_USER_PAGE_SHIFT_32_LEGACY + _COMM_PAGE_RW_OFFSET)) = (uint8_t) page_shift_user32;
	*((uint8_t*)(_COMM_PAGE_USER_PAGE_SHIFT_32 + _COMM_PAGE_RO_OFFSET)) = (uint8_t) page_shift_user32;
	/* HAKIOS-LAFA-4K-USERMAP: tell user space the truth (4 KiB), not the 16 KiB this used to say */
	*((uint8_t*)(_COMM_PAGE_USER_PAGE_SHIFT_64_LEGACY + _COMM_PAGE_RW_OFFSET)) = (uint8_t) FOURK_PAGE_SHIFT;
	*((uint8_t*)(_COMM_PAGE_USER_PAGE_SHIFT_64 + _COMM_PAGE_RO_OFFSET)) = (uint8_t) FOURK_PAGE_SHIFT;
"""


def patch_usermap(root):
    f = root / "bsd/kern/mach_loader.c"
    f2 = root / "osfmk/arm/commpage/commpage.c"
    if not f.exists() or not f2.exists():
        print("patch-4k: mach_loader.c / commpage.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK_USERMAP in text:
        print("patch-4k: user map already patched")
    elif MACH_OLD in text:
        f.write_text(text.replace(MACH_OLD, MACH_NEW, 1), errors="surrogateescape")
        print("patch-4k: mach_loader.c: 64-bit user maps now use 4 KiB entries")
    else:
        print("patch-4k: MISS: mach_loader.c page-shift block is not the expected shape")
        return 1
    text = f2.read_text(errors="surrogateescape")
    if MARK_USERMAP in text:
        print("patch-4k: commpage already patched")
    elif COMM_OLD in text:
        f2.write_text(text.replace(COMM_OLD, COMM_NEW, 1), errors="surrogateescape")
        print("patch-4k: commpage.c: user page shift now reports 4 KiB")
    else:
        print("patch-4k: MISS: commpage.c page-shift block is not the expected shape")
        return 1
    return 0


def main(root):
    if len(sys.argv) < 2:
        print("usage: patch-kernel-4k-pages.py <xnu tree>")
        raise SystemExit(2)
    root = pathlib.Path(sys.argv[1])
    rc = patch_page_selection(root)
    rc |= patch_board_headers(root)
    rc |= patch_usermap(root)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
