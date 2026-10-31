#!/usr/bin/env python3
"""干净的 MMU 测试：开 MMU → 立刻关回去 → 再画绿块（中间不触碰任何内存）。

开 MMU 之后 framebuffer 不在映射里，写它会 fault，所以旧版"画绿块"的实验不成立。
这里在两次 SCTLR 写之间只做寄存器操作：

- 绿块出现  → MMU 打开这一条指令本身是好的（TCR/TTBR/页表能走通），问题在之后；
- 没有绿块  → MMU 打开当场 fault。

    python3 ci/patch-kernel-mmu-roundtrip.py <xnu 源码树>
"""
import sys
from pathlib import Path

BLOCK = '''	/* HAKIOS: turn the MMU back off at once, then draw (no memory access in between) */
	mrs	x9, sctlr_el1
	bic	x9, x9, #1
	msr	sctlr_el1, x9
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
    if "HAKIOS: turn the MMU back off" in text:
        print("patch-roundtrip: already patched")
        return 0
    if ANCHOR not in text:
        print("patch-roundtrip: anchor not found", file=sys.stderr)
        return 1
    off = 2000 * 5760
    text = text.replace(ANCHOR, ANCHOR + BLOCK.format(off=off), 1)
    src.write_text(text, encoding="utf-8")
    print("patch-roundtrip: MMU on/off round trip + green block at row 2000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
