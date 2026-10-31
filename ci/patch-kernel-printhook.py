#!/usr/bin/env python3
"""内核侧：打开 MMU 之前，从 0x41CFF00 取出 loader 的 print 并打印三个寄存器。

输出：
    TCR xxxx SCTLR xxxx MMFR xxxx

    python3 ci/patch-kernel-printhook.py <xnu 源码树>
"""
import sys
from pathlib import Path

BLOCK = '''	/* HAKIOS: print our early registers through the loader's print(), whose address
	 * the loader leaves at boot_args + 0xF00.  x20 is the boot_args VIRTUAL address
	 * here, and the MMU is still off: convert back to physical with x22/x23 first. */
	stp	x9, x30, [sp, #-16]!
	sub	x9, x20, x22
	add	x9, x9, x23
	ldr	x9, [x9, #0xF00]
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

	/* HAKIOS HOLD: do not enable the MMU; stay here so the line stays on the screen
	 * until the 60 s watchdog resets the board. */
8:	wfi
	b	8b
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
