#!/usr/bin/env python3
"""内核自己画屏：把 bootloader 的 framebuffer 映射成设备内存并填满渐变色。

第一次由内核（而不是 loader）控制屏幕。

    python3 ci/patch-kernel-fb.py <xnu 源码树>
"""
import sys
from pathlib import Path

FB_FUNC = r'''
/*
 * HAKIOS-LAFA: the panel is already on (the bootloader left a framebuffer at v_baseAddr); this maps
 * it as device memory and fills it, so what is on the screen comes from the kernel itself.
 */
void
lafa_fb_fill(void)
{
	Boot_Video *v = &PE_state.video;
	uint64_t n, i;
	uint32_t *p;
	vm_offset_t kva;

	if (v->v_baseAddr == 0 || v->v_rowBytes == 0 || v->v_height == 0) {
		printf("lafa-fb: no framebuffer (%x %x %x)\n", (uint64_t)v->v_baseAddr,
		    (uint64_t)v->v_rowBytes, (uint64_t)v->v_height);
		return;
	}
	n = (uint64_t)v->v_rowBytes * (uint64_t)v->v_height;
	printf("lafa-fb: base %x row %x height %x, %x bytes\n", (uint64_t)v->v_baseAddr,
	    (uint64_t)v->v_rowBytes, (uint64_t)v->v_height, n);
	/* TEST: one page first: is the mapping usable at all? */
	kva = (vm_offset_t)ml_io_map((vm_offset_t)v->v_baseAddr, 4096);
	printf("lafa-fb: one page -> %x\n", (uint64_t)kva);
	if (kva == 0) {
		return;
	}
	((uint32_t *)kva)[0] = 0xffff0000u;
	((uint32_t *)kva)[1] = 0xff00ff00u;
	printf("lafa-fb: one page written\n");
	return;
	kva = (vm_offset_t)ml_io_map((vm_offset_t)v->v_baseAddr, (vm_size_t)n);
	if (kva == 0) {
		printf("lafa-fb: ml_io_map(%x, %x) failed\n", (uint64_t)v->v_baseAddr, n);
		return;
	}
	p = (uint32_t *)kva;
	for (i = 0; i < n / 4; i++) {
		uint32_t x = (uint32_t)(i % (v->v_rowBytes / 4)), y = (uint32_t)(i / (v->v_rowBytes / 4));
		p[i] = 0xff000000u | ((x * 255u / (v->v_rowBytes / 4 ? v->v_rowBytes / 4 : 1)) << 16) |
		    ((y * 255u / (v->v_height ? v->v_height : 1)) << 8) | 0x40u;
	}
	printf("lafa-fb: %d x %d, %d bytes at %x mapped and filled\n", (uint64_t)v->v_width,
	    (uint64_t)v->v_height, n, (uint64_t)v->v_baseAddr);
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
        # needs the video struct and ml_io_map
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
    # declaration near the other externs
    b = b.replace("void bsd_utaskbootstrap(void);",
                  "void bsd_utaskbootstrap(void);\nextern void lafa_fb_fill(void);", 1)
    bi.write_text(b, encoding="utf-8", errors="surrogateescape")
    print("patch-fb: kernel framebuffer fill inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
