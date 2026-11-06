#!/usr/bin/env python3
"""内核自己画屏（方案 B）：让 xnu 自带的 video console 去映射 framebuffer 并写字。

之前用 ml_io_map 直接映射 0xFC800000 写像素会 data abort（1 页也崩），
这里改成 xnu 原生的路径：把 v_baseAddr 标成"物理地址"（bit0），
让 initialize_screen() 自己映射，然后用 vcputc() 写字。

    python3 ci/patch-kernel-fb.py <xnu 源码树>
"""
import sys
from pathlib import Path

FB_FUNC = r'''
/*
 * HAKIOS-LAFA: the kernel's own video console drives the panel. The bootloader left a framebuffer at
 * v_baseAddr and xnu maps it itself when the low bit says "this is a physical address"; writing to it
 * through ml_io_map() faulted, so this is the path that uses (the same one the arm ports take).
 */
#include <console/video_console.h>

static void
lafa_fb_putline(const char *s)
{
	for (; *s != 0; s++) {
		vcputc(*s);
	}
	vcputc('\n');
}

void
lafa_fb_fill(void)
{
	Boot_Video *v = &PE_state.video;

	if (v->v_baseAddr == 0 || v->v_rowBytes == 0 || v->v_height == 0) {
		printf("lafa-fb: no framebuffer (%x %x %x)\n", (uint64_t)v->v_baseAddr,
		    (uint64_t)v->v_rowBytes, (uint64_t)v->v_height);
		return;
	}
	printf("lafa-fb: base %x row %x height %x depth %x\n", (uint64_t)v->v_baseAddr,
	    (uint64_t)v->v_rowBytes, (uint64_t)v->v_height, (uint64_t)v->v_depth);
	v->v_baseAddr |= 1;             /* "the address is physical: map it" */
	initialize_screen(NULL, v);
	printf("lafa-fb: initialize_screen returned\n");
	lafa_fb_putline("hAKiOS lafa: the kernel drew this");
	lafa_fb_putline("SM8850 / realme GT8 Pro");
	lafa_fb_putline("framebuffer mapped by the kernel video console");
	printf("lafa-fb: text drawn\n");
}
'''

CALL = """\t/* HAKIOS-LAFA: let the kernel own the screen for the first time */
\t{lafa_fb_fill();}

\tbsd_init_kprintf("vfs_mountroot\\n");
"""

ANCHOR_BSD = '\tbsd_init_kprintf("vfs_mountroot\\n");\n'


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    lr = root / "osfmk" / "arm64" / "loader_return.c"
    bi = root / "bsd" / "kern" / "bsd_init.c"
    text = lr.read_text(encoding="utf-8", errors="surrogateescape")
    if "lafa_fb_fill" in text:
        print("patch-fb: loader_return.c already patched")
    else:
        text = text.replace('#include <pexpert/pexpert.h>',
                            '#include <pexpert/pexpert.h>\n#include <pexpert/boot.h>', 1)
        if "#include <pexpert/boot.h>" not in text:
            text = '#include <pexpert/boot.h>\n' + text
        text += FB_FUNC
        lr.write_text(text, encoding="utf-8", errors="surrogateescape")

    b = bi.read_text(encoding="utf-8", errors="surrogateescape")
    if "lafa_fb_fill" in b:
        print("patch-fb: bsd_init.c already patched")
        return 0
    if ANCHOR_BSD not in b:
        print("patch-fb: bsd_init anchor not found", file=sys.stderr)
        return 1
    b = b.replace(ANCHOR_BSD, CALL, 1)
    b = b.replace("void bsd_utaskbootstrap(void);",
                  "void bsd_utaskbootstrap(void);\nextern void lafa_fb_fill(void);", 1)
    bi.write_text(b, encoding="utf-8", errors="surrogateescape")
    print("patch-fb: kernel video console path inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
