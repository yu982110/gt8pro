#!/usr/bin/env python3
"""自包含寄存器点阵（大块、拆两行）：完全在内核 asm 里画，不调用 loader、不依赖任何地址。

每个寄存器画 64 个比特，分两行：
  第 1 行：bit0..bit31（左→右）
  第 2 行：bit32..bit63
每块 40×32 像素，红=1、黑=0；整行 32*40 = 1280 像素。

  y=1700/1740  TCR_EL1
  y=1800/1840  SCTLR_EL1
  y=1900/1940  ID_AA64MMFR0_EL1

画完原地 wfi（不打开 MMU），屏幕保留 60 秒。

    python3 ci/patch-kernel-bars.py <xnu 源码树>
"""
import sys
from pathlib import Path

ROW_PITCH = 40 * 5760          # 40 rows
HALF_ADVANCE = 32 * 160        # 32 blocks * 40 px * 4 bytes
SECOND_ROW = ROW_PITCH - HALF_ADVANCE   # 0x37000

LOOP = '''	mov	x11, #0
1:	and	x12, x9, #1
	cmp	x12, #0
	csel	w12, w15, w16, ne
	mov	x13, x10
	mov	x14, #32
2:	mov	x17, #40
3:	str	w12, [x13], #4
	subs	x17, x17, #1
	b.ne	3b
	add	x13, x13, x18
	subs	x14, x14, #1
	b.ne	2b
	add	x10, x10, #160
	lsr	x9, x9, #1
	add	x11, x11, #1
	cmp	x11, #32
	b.ne	1b
'''


def dump(reg, y):
    fb = 0xFC800000 + y * 5760
    return f'''	/* HAKIOS BARS {reg} rows {y}/{y + 40} */
	mrs	x9, {reg}
	movz	x10, #0xFC80, lsl #16
	ldr	x11, =0x{fb:X}
	add	x10, x10, x11
	mov	w15, #0xFF0000
	mov	w16, #0
	mov	x18, #5600
{LOOP}	/* second half */
	ror	x9, x9, #32
	add	x10, x10, #0x{SECOND_ROW:X}
{LOOP}'''


BLOCKS = "".join(dump(reg, y) for reg, y in [
    ("tcr_el1", 1700),
    ("sctlr_el1", 1800),
    ("id_aa64mmfr0_el1", 1900),
])

ANCHOR = "\t// Enable caches, MMU, ROP and JOP\n"
TAIL = '''	/* HAKIOS HOLD: stay here (MMU still off) so the bars remain on the screen */
8:	wfi
	b	8b
'''


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "osfmk" / "arm64" / "start.s"
    text = src.read_text(encoding="utf-8")
    if "HAKIOS BARS" in text:
        print("patch-bars: already patched")
        return 0
    if ANCHOR not in text:
        print("patch-bars: anchor not found", file=sys.stderr)
        return 1
    text = text.replace(ANCHOR, BLOCKS + TAIL + ANCHOR, 1)
    src.write_text(text, encoding="utf-8")
    print("patch-bars: three register dumps inserted at y=1700/1800/1900")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
