#!/usr/bin/env python3
"""KVA 映射测试：MMU 开着的时候读一个**内核虚拟地址**（x22 = virtBase），再关 MMU 画绿块。

- 绿块出现 → KVA（TTBR1）映射是好的，问题在更后面（arm_init/其他）；
- 没有绿块 → KVA 映射坏了（TTBR1/TCR.T1SZ/页表），这就是内核死在 MMU 后的原因。

    python3 ci/patch-kernel-kva-read.py <xnu 源码树>
"""
import sys
from pathlib import Path

BLOCK = '''	/* HAKIOS KVA TEST: read through TTBR1 with the MMU on, then turn it off and draw */
	ldr	x9, [x22]
	mrs	x10, sctlr_el1
	bic	x10, x10, #1
	msr	sctlr_el1, x10
	isb	sy
	movz	x9, #0xFC80, lsl #16
	ldr	x10, =0x{off:X}
	add	x9, x9, x10
	mov	w11, #0x00FF00
	mov	x12, #64
1:	str	w11, [x9], #4
	subs	x12, x12, #1
	b.ne	1b
2:	wfi
	b	2b
'''

ANCHOR = "\tMSR_SCTLR_EL1_X0\n\tisb\t\tsy\n"


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "osfmk" / "arm64" / "start.s"
    text = src.read_text(encoding="utf-8")
    if "HAKIOS KVA TEST" in text:
        print("patch-kva: already patched")
        return 0
    if ANCHOR not in text:
        print("patch-kva: anchor not found", file=sys.stderr)
        return 1
    off = 2000 * 5760
    text = text.replace(ANCHOR, ANCHOR + BLOCK.format(off=off), 1)
    src.write_text(text, encoding="utf-8")
    print("patch-kva: KVA read after MMU-on + green marker")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
