#!/usr/bin/env python3
"""决定性实验：打开 MMU 之后**不跳 KVA**，就在 V=P 映射上画一块色块并停住。

目的：把"MMU 打开本身失败"和"KVA 映射失败"分开。

- 画出绿块   → MMU 已开、V=P 映射正常、SCTLR/TCR 没问题 → 死在 `ret` 到 arm_init（KVA）。
- 什么都没有 → MMU 打开就炸（V=P 页表或 SCTLR 位）。

    python3 ci/patch-kernel-vp-after-mmu.py <xnu 源码树>
"""
import sys
from pathlib import Path

BLOCK = '''	/* HAKIOS: after the MMU is on, stay on the V=P mapping and draw a green block */
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
    if "HAKIOS: after the MMU is on" in text:
        print("patch-vp: already patched")
        return 0
    if ANCHOR not in text:
        print("patch-vp: anchor not found", file=sys.stderr)
        return 1
    off = 2000 * 5760            # row 2000, below any existing dumps
    text = text.replace(ANCHOR, ANCHOR + BLOCK.format(off=off), 1)
    src.write_text(text, encoding="utf-8")
    print("patch-vp: V=P after-MMU marker inserted at row 2000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
