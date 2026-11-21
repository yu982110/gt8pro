#!/usr/bin/env python3
"""Reserve the physmap VA that arm_vm_physmap_slide() can eat, as the SM8850 port does.

This kernel reaches grade_binary and then stops dead the moment mockfs wants a memory-backed
device: pager_map_to_phys_contiguous() takes the device's physical pages straight from the VM
allocator, and those pages are addressed through the physmap (phystokv).  arm_vm_physmap_slide()
preserves each physical address's block offset, so every PTOV table entry can consume up to one
extra twig of virtual address space -- and the aperture bound here does not allow for it.  The
high end of the physmap is then unmapped, pages land there, and the kernel takes a fault in a
path that never comes back: no output, no timer, `oem stop` unreachable.  The extent-table boot
does not go through the pager at all, which is why that one still reaches grade_binary.

The working SM8850 port carries exactly this reservation ("each PTOV entry can therefore consume
up to one extra twig of VA space. Reserve that space in both the aperture bound and its L1
allocation, including the ARM_LARGE_MEMORY configuration used by its non-PPL kernel").

usage: patch-kernel-physmap-padding.py <xnu source dir>
"""
import pathlib
import sys

MARK = "HAKIOS-PHYSMAP-PADDING"

DEFINE_OLD = "#define ARM64_PHYSMAP_SLIDE_MASK  (ARM64_PHYSMAP_SLIDE_RANGE - 1)\n"
DEFINE_NEW = """#define ARM64_PHYSMAP_SLIDE_MASK  (ARM64_PHYSMAP_SLIDE_RANGE - 1)

#if XNU_MONITOR
#define ARM64_PHYSMAP_ALIGNMENT_PADDING 0ULL
#else
/*
 * HAKIOS-PHYSMAP-PADDING: arm_vm_physmap_slide() preserves each physical address's block
 * offset, so each PTOV entry can consume up to one extra twig of VA space. Reserve that
 * space in both the aperture bound and its L1 allocation, including the
 * ARM_LARGE_MEMORY configuration used by this port's non-PPL kernel. Without it the high
 * end of the physmap is not mapped, and pages the VM hands to a memory-backed pager
 * (mockfs) fault in a path that never returns -- which is where this kernel stops.
 */
#define ARM64_PHYSMAP_ALIGNMENT_PADDING (PTOV_TABLE_SIZE * ARM_TT_TWIG_SIZE)
#endif
"""

L1_OLD = "\tunsigned long physmap_l1_entries = ((real_phys_size + ARM64_PHYSMAP_SLIDE_RANGE) >> ARM_TT_L1_SHIFT) + 1;\n"
L1_NEW = "\tunsigned long physmap_l1_entries = ((real_phys_size + ARM64_PHYSMAP_SLIDE_RANGE + ARM64_PHYSMAP_ALIGNMENT_PADDING) >> ARM_TT_L1_SHIFT) + 1;\n"

END1_OLD = "\tphysmap_end = physmap_base + real_phys_size;\n"
END1_NEW = "\tphysmap_end = physmap_base + real_phys_size + ARM64_PHYSMAP_ALIGNMENT_PADDING;\n"

END2_OLD = """\tstatic_memory_end = physmap_base + mem_size + (PTOV_TABLE_SIZE * ARM_TT_TWIG_SIZE); // worst possible case for block alignment
\tphysmap_end = physmap_base + real_phys_size + (PTOV_TABLE_SIZE * ARM_TT_TWIG_SIZE);
"""
END2_NEW = """\tstatic_memory_end = physmap_base + mem_size + ARM64_PHYSMAP_ALIGNMENT_PADDING; // worst possible case for block alignment
\tphysmap_end = physmap_base + real_phys_size + ARM64_PHYSMAP_ALIGNMENT_PADDING;
"""


def main(root):
    f = pathlib.Path(root) / "osfmk/arm64/arm_vm_init.c"
    if not f.exists():
        print("patch-physmap-padding: osfmk/arm64/arm_vm_init.c not found")
        return 1
    t = f.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-physmap-padding: already there")
        return 0
    rc = 0
    for old, new, name in ((DEFINE_OLD, DEFINE_NEW, "define the padding"),
                           (L1_OLD, L1_NEW, "L1 allocation"),
                           (END1_OLD, END1_NEW, "ARM_LARGE_MEMORY aperture bound"),
                           (END2_OLD, END2_NEW, "physmap_end bound")):
        if old not in t:
            print("patch-physmap-padding: MISS anchor: %s" % name)
            rc = 1
            continue
        t = t.replace(old, new, 1)
        print("patch-physmap-padding: %s" % name)
    f.write_text(t, errors="surrogateescape")
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
