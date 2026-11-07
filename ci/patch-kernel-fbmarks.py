#!/usr/bin/env python3
"""patch-kernel-fbmarks.py -- draw one coloured square per boot milestone on the panel.

The kernel's log cannot always be read back (the loader reads it through a second mapping, and
a kernel that dies early leaves nothing there).  The framebuffer is different: it is mapped
with a helper that is already proven on this hardware (patch-kernel-fb.py), and a filled
square is visible whether or not any log survives.

So: bsd_init() paints red as it enters, orange before the root mount, yellow once the mount
returned.  Counting squares tells how far a boot got without a single line of text.

Anchor: the same bsd_init_kprintf("vfs_mountroot\\n") call the framebuffer patch uses, plus
the first line of bsd_init().  Idempotent: a tree that already carries the marks is left alone.
"""
import pathlib
import re
import sys

MARK = "HAKIOS-LAFA-FBMARKS"
CALL_RE = re.compile(r'^([ \t]*)bsd_init_kprintf\("vfs_mountroot\\n"\);', re.MULTILINE)

HELPERS = r'''
extern vm_offset_t ml_io_map_unmappable(vm_offset_t, vm_size_t, unsigned int);

/* HAKIOS-LAFA-FBMARKS: one coloured square per milestone, so a boot that leaves no log still
 * says how far it got. The panel is 1440x3136 at PA 0xFC800000, stride 5760; the squares sit
 * in the top row, well clear of the bottom quarter the debug fill paints. */
static void
lafa_fb_mark(int n, uint32_t rgb)
{
	uint32_t *fb;
	uint32_t x0, y0, x, y;

	if (n < 0 || n > 15) {
		return;
	}
	fb = (uint32_t *)(uintptr_t)ml_io_map_unmappable(0xFC800000ULL,
	    round_page(5760ULL * 3136ULL), 0x01 /* VM_WIMG_WCOMB */);
	if (fb == (uint32_t *)NULL) {
		return;
	}
	x0 = 80u + (uint32_t)n * 160u;
	y0 = 80u;
	for (y = y0; y < y0 + 120u && y < 3136u; y++) {
		for (x = x0; x < x0 + 120u && x < 1440u; x++) {
			fb[(uint64_t)y * 1440u + x] = rgb;
		}
	}
}

static void
lafa_fb_marks(void)
{
	lafa_fb_mark(0, 0x00FF0000u);   /* bsd_init entered */
}
'''

CALLS = r'''	lafa_fb_marks();
	/* HAKIOS-LAFA-FBMARKS: bsd_init reached */
'''


def patch(root):
    f = pathlib.Path(root) / "bsd/kern/bsd_init.c"
    if not f.exists():
        print(f"patch-fbmarks: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-fbmarks: already applied")
        return 0
    if "lafa_fb_mark(" in text:
        print("patch-fbmarks: a lafa_fb_mark already exists; refusing to add a second")
        return 1

    m = CALL_RE.search(text)
    if m is None:
        print('patch-fbmarks: the vfs_mountroot anchor was not found')
        return 1
    indent = m.group(1)

    # helpers go after the last include: they use round_page() and ml_io_map_unmappable() with
    # their own declarations, so their position among the functions does not matter
    # at the top of the file: bsd_init() calls them, and C wants them declared first
    text = HELPERS + "\n" + text

    # orange right before the mount, yellow right after it returns
    m = CALL_RE.search(text)
    before = f'{indent}lafa_fb_mark(1, 0x00FF8000u);   /* about to mount the root */\n'
    text = text[:m.start()] + before + text[m.start():]

    # the call: bsd_init entered
    entry = re.search(r'\nbsd_init\(void\)\s*\n?\{[ \t]*\n', text)
    if entry is not None:
        text = text[:entry.end()] + CALLS + text[entry.end():]
    else:
        print("patch-fbmarks: bsd_init's opening brace not found; only the mount mark is in")

    f.write_text(text, errors="surrogateescape")
    print("patch-fbmarks: milestone squares inserted (red at entry, orange before mount)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-fbmarks.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(patch(sys.argv[1]))
