#!/usr/bin/env python3
"""内核侧：打开 MMU 之前，从 0x41CFF00 取出 loader 的 print 并打印三个寄存器。

输出：
    TCR xxxx SCTLR xxxx MMFR xxxx

    python3 ci/patch-kernel-printhook.py <xnu 源码树>
"""
import sys
from pathlib import Path

BLOCK = '''	/* HAKIOS: print our early registers through the loader's print() */
	stp	x9, x30, [sp, #-16]!
	ldr	x9, =0x41CFF00
	ldr	x9, [x9]
	cbz	x9, 7f
	mrs	x1, tcr_el1
	mrs	x2, sctlr_el1
	mrs	x3, id_aa64mmfr0_el1
	adr	x0, 6f
	blr	x9
	b	7f
6:	.asciz "TCR %x SCTLR %x MMFR %x\\n"
	.align	3
7:	ldp	x9, x30, [sp], #16
'''

ANCHOR = "\t// Enable caches, MMU, ROP and JOP\n"


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "osfmk" / "arm64" / "start.s"
    text = src.read_text(encoding="utf-8")
    if "HAKIOS: print our early registers" in text:
        print("patch-kernel-printhook: already patched")
        return 0
    if ANCHOR not in text:
        print("patch-kernel-printhook: anchor not found", file=sys.stderr)
        return 1
    text = text.replace(ANCHOR, BLOCK + ANCHOR, 1)
    src.write_text(text, encoding="utf-8")
    print("patch-kernel-printhook: register print inserted before the MMU is enabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
