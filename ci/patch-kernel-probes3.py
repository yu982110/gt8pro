#!/usr/bin/env python3
"""寄存器转像素探针（大块版）：打开 MMU 之前，把三个系统寄存器画成 64 个 16×8 色块。

  y=210  TCR_EL1
  y=240  SCTLR_EL1
  y=270  ID_AA64MMFR0_EL1

红 = 1，黑 = 0，**bit0 在最左边**；每个比特 16 像素宽、8 像素高，整行 1024 像素。

    python3 ci/patch-kernel-probes3.py <xnu 源码树>
"""
import sys
from pathlib import Path

def probe(fb_const, reg):
    return f'''	/* HAKIOS REGDUMP {reg} */
	mrs	x9, {reg}
	ldr	x10, ={fb_const}
	mov	x11, #64
	ldr	x18, =5760
	mov	w15, #0xFF0000
	mov	w16, #0x000000
1:	and	w12, w9, #1
	cmp	w12, #0
	csel	w12, w15, w16, ne
	mov	x13, x10
	mov	x14, #8
2:	mov	x17, #16
3:	str	w12, [x13], #4
	subs	x17, x17, #1
	b.ne	3b
	add	x13, x13, x18
	sub	x13, x13, #64
	subs	x14, x14, #1
	b.ne	2b
	add	x10, x10, #64
	lsr	x9, x9, #1
	subs	x11, x11, #1
	b.ne	1b
'''

ROWS = [
    ("0xFC927800", "tcr_el1"),           # 210
    ("0xFC951800", "sctlr_el1"),         # 240
    ("0xFC97B800", "id_aa64mmfr0_el1"),  # 270
]

ANCHOR = "\t// Enable caches, MMU, ROP and JOP\n"


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "osfmk" / "arm64" / "start.s"
    text = src.read_text(encoding="utf-8")
    if "HAKIOS REGDUMP" in text:
        print("patch-probes3: already patched")
        return 0
    if ANCHOR not in text:
        print("patch-probes3: anchor not found", file=sys.stderr)
        return 1
    block = "".join(probe(fb, reg) for fb, reg in ROWS)
    text = text.replace(ANCHOR, block + ANCHOR, 1)
    src.write_text(text, encoding="utf-8")
    print("patch-probes3: big-block register dumps added (rows 210/240/270)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
