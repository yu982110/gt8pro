#!/usr/bin/env python3
"""HAKIOS-LAFA: paint the 1440x3136 panel framebuffer from bsd_init().

The framebuffer at PA 0xFC800000 is outside the kernel's physmap
(boot_args physBase=0x8C0000000, memSize ~3.4 GB), so ml_static_ptovirt()
cannot reach it, and ml_io_map() is limited to the 8 MB io_submap.  The
mapping has to come from kernel_map, after vm_mem_bootstrap() has made
kernel_map live, which is exactly where bsd_init() runs.

This script inserts, immediately before bsd_init()'s
    bsd_init_kprintf("vfs_mountroot\\n");
a call to lafa_fb_fill(), and adds that function (plus the headers it
needs) to bsd/kern/bsd_init.c (the real path; xnu-8796.121.2 has no
bsd/dev/arm/bsd_init.c).

Usage:
    python3 ci/patch-kernel-fb.py <xnu-source-tree>

The script is idempotent: running it twice on the same tree is a no-op.
It refuses to touch a tree that already contains a *different*
lafa_fb_fill (e.g. the old loader_return.c/video-console experiment), so
a stale tree cannot silently keep the old, broken mapping.
"""
import re
import sys
from pathlib import Path

MARK = "HAKIOS-LAFA-FB"
FUNC = "lafa_fb_fill"

# Anchors in bsd/kern/bsd_init.c (xnu-8796.121.2, with or without hakios.patch;
# hakios.patch only changes DEBUG_BSDINIT and adds a devfs hunk after this point).
ANCHOR_INCLUDE = "#include <mach/vm_param.h>\n"
ANCHOR_EXTERN = "extern lck_rw_t rootvnode_rw_lock;\n"
CALL_RE = re.compile(r'^([ \t]*)bsd_init_kprintf\("vfs_mountroot\\n"\);', re.MULTILINE)
BSD_INIT_RE = re.compile(r'^void\nbsd_init\(void\)\n\{', re.MULTILINE)
BSD_INIT_COMMENT_RE = re.compile(
    r'^/\*\n \* This function is called very early on in the Mach startup',
    re.MULTILINE,
)

INCLUDES = """\

/* HAKIOS-LAFA-FB: kernel_map mapping for the panel framebuffer */
#include <vm/pmap.h>            /* kernel_pmap; VM_WIMG_* via BSD_KERNEL_PRIVATE */
#include <machine/atomic.h>     /* DSB_ISH */
#include <arm/machine_routines.h> /* ml_io_map_* (private section, see below) */
"""

FUNC_BLOCK = r'''
/*
 * HAKIOS-LAFA-FB: the panel framebuffer at PA 0xFC800000 is outside the
 * kernel's physmap (boot_args physBase 0x8C0000000, memSize ~3.4 GB), so
 * ml_static_ptovirt() cannot reach it.  bsd_init() runs after
 * vm_mem_bootstrap(), so kernel_map is live and ml_io_map_unmappable() can
 * create a proper WCOMB mapping for the whole buffer:
 *
 *     1440 x 3136, stride 5760 bytes (1440 pixels), 32 bpp => 18,063,360 B
 *
 * P0 (bring-up) order matters: map, print va + the PTE translation, write
 * ONE pixel, fence, and only then fill the screen.  If the one-pixel write
 * faults, the faulting address tells us whether the mapping or the bus is
 * wrong before 18 MB of stores are attempted.
 *
 * <arm/machine_routines.h> guards the IO-map prototypes with
 * PEXPERT_KERNEL_PRIVATE || MACH_KERNEL_PRIVATE, and <vm/pmap.h> guards
 * pmap_find_phys() with MACH_KERNEL_PRIVATE; BSD sources are built with only
 * BSD_KERNEL_PRIVATE.  bsd/dev/memdev.c solves this by declaring
 * pmap_find_phys() itself, so do the same for both functions.
 */
extern vm_offset_t ml_io_map_unmappable(vm_offset_t phys_addr, vm_size_t size,
    uint32_t flags);
extern ppnum_t pmap_find_phys(pmap_t pmap, addr64_t va);

#ifndef DSB_ISH
#define DSB_ISH 0xb             /* DSB ISH; normally from <machine/atomic.h> */
#endif

static void
lafa_fb_fill(void)
{
	static int lafa_fb_done;
	vm_offset_t va;
	vm_offset_t pa;
	volatile uint32_t *px;

	/* bsd_init() retries vfs_mountroot() in a loop; paint only once. */
	if (lafa_fb_done) {
		return;
	}

	va = ml_io_map_unmappable(0xFC800000UL,
	    round_page((vm_size_t)5760 * 3136), VM_WIMG_WCOMB);
	if (va == 0) {
		printf("lafa-fb: ml_io_map_unmappable(0xfc800000, %lu) failed\n",
		    (unsigned long)round_page((vm_size_t)5760 * 3136));
		return;
	}

	px = (volatile uint32_t *)va;

	/*
	 * pmap_find_phys() is a page-table lookup, not a framebuffer read, so
	 * this does not violate the "no CPU read-back from the panel" rule.
	 * phys == 0 is a strong sign that no PTE was installed, but the actual
	 * test is still the four-byte store below: AT/page-table walks can be
	 * unavailable on some hypervisors even for a valid mapping.
	 */
	pa = ptoa(pmap_find_phys(kernel_pmap, va));
	printf("lafa-fb: pa fc800000 va %lx phys %lx\n", (unsigned long)va,
	    (unsigned long)pa);
	if (pa == 0) {
		printf("lafa-fb: warning: pmap_find_phys returned 0, probing anyway\n");
	}

	/* P0: exactly four bytes first.  A fault here is a mapping/bus fault. */
	px[0] = 0xffff00ffu;
	__builtin_arm_dsb(DSB_ISH);
	printf("lafa-fb: probe ok\n");

	/* P1: only the bottom quarter. The top is where the loader's live drain draws the kernel's
	 * log, and filling the whole screen would paint over the one thing worth reading. */
	for (int y = 2352; y < 3136; y++) {
		for (int x = 0; x < 1440; x++) {
			px[y * 1440 + x] = 0xff204080;
		}
	}
	__builtin_arm_dsb(DSB_ISH);

	lafa_fb_done = 1;
	printf("lafa-fb: filled\n");
}
'''


def fail(message: str) -> int:
    print(f"patch-kernel-fb: {message}", file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} <xnu-source-tree>", file=sys.stderr)
        return 2

    root = Path(sys.argv[1])
    # xnu-8796.121.2 has bsd/kern/bsd_init.c (there is no bsd/dev/arm one),
    # but accept the latter too for a fork that moved the file.
    candidates = [
        root / "bsd" / "kern" / "bsd_init.c",
        root / "bsd" / "dev" / "arm" / "bsd_init.c",
    ]
    bsd_init = next((p for p in candidates if p.is_file()), None)
    if bsd_init is None:
        return fail(f"{candidates[0]} not found (is {root} an xnu source tree?)")

    with bsd_init.open("r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        original = f.read()

    # Keep the tree's original line endings (xnu tarballs are LF; a Windows
    # checkout may be CRLF).  Edit in LF, restore afterwards.
    crlf = "\r\n" in original
    text = original.replace("\r\n", "\n") if crlf else original

    has_marker = MARK in text
    has_func = re.search(r'\bstatic\s+void\s*\n?\s*lafa_fb_fill\s*\(', text) is not None
    has_call = re.search(r'\{\s*lafa_fb_fill\(\);\s*\}', text) is not None

    if has_marker and has_func and has_call:
        print("patch-kernel-fb: already patched, nothing to do")
        return 0

    # A tree carrying the old lafa_fb_fill (loader_return.c experiment) or a
    # half-applied new patch must not be silently skipped.
    if "lafa_fb_fill" in text:
        return fail(
            "bsd_init.c already contains lafa_fb_fill but not this patch's "
            "marker; use a clean xnu tree (the old video-console experiment "
            "must be reverted first)"
        )

    call_matches = list(CALL_RE.finditer(text))
    if len(call_matches) != 1:
        return fail(
            'expected exactly one bsd_init_kprintf("vfs_mountroot\\n"); '
            f"anchor, found {len(call_matches)}"
        )

    # 1. Call site first: this is the only edit that depends on a byte offset,
    #    so do it before any insertion changes the offsets.  Honor the existing
    #    indentation (the line is inside the while (TRUE) loop).
    m = call_matches[0]
    indent = m.group(1)
    call = (
        f"{indent}/* {MARK}: map and paint the panel before mountroot */\n"
        f"{indent}{{ {FUNC}(); }}\n\n"
    )
    text = text[:m.start()] + call + text[m.start():]

    # 2. Headers: vm/pmap.h for kernel_pmap and (under BSD_KERNEL_PRIVATE) the
    #    arm WIMG flags; machine/atomic.h for DSB_ISH.  The two prototypes
    #    hidden by private build flags are declared in the function block.
    include_count = text.count(ANCHOR_INCLUDE)
    if include_count != 1:
        return fail(
            f"expected exactly one {ANCHOR_INCLUDE.strip()!r} anchor, "
            f"found {include_count}"
        )
    text = text.replace(ANCHOR_INCLUDE, ANCHOR_INCLUDE + INCLUDES, 1)

    # 3. Implementation, after the extern declarations and before bsd_init().
    if ANCHOR_EXTERN in text:
        text = text.replace(ANCHOR_EXTERN, ANCHOR_EXTERN + FUNC_BLOCK, 1)
    else:
        # Fallback for a tree whose extern block was reorganized: put the
        # function before bsd_init()'s own comment, so that comment still
        # describes bsd_init().
        m = BSD_INIT_COMMENT_RE.search(text)
        if m is None:
            m = BSD_INIT_RE.search(text)
        if m is None:
            return fail("could not find a safe place for lafa_fb_fill()")
        text = text[:m.start()] + FUNC_BLOCK.lstrip("\n") + "\n" + text[m.start():]

    if crlf:
        text = text.replace("\n", "\r\n")

    with bsd_init.open("w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(text)

    print(f"patch-kernel-fb: patched {bsd_init}")
    print("patch-kernel-fb: call inserted before bsd_init_kprintf(\"vfs_mountroot\\n\")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
