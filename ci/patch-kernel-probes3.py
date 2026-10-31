#!/usr/bin/env python3
"""寄存器转像素探针：打开 MMU 之前，把三个系统寄存器各画成 64 个像素。

  y=210  TCR_EL1
  y=212  SCTLR_EL1
  y=214  ID_AA64MMFR0_EL1

每个像素 4 字节；红 = 1，黑 = 0；**bit0 在最左边**。一张照片就能读出三个值，
直接和硬件能力对照（TCR 的 TG0/IPS、SCTLR 的 M/C/I、MMFR0 的粒度支持）。

    python3 ci/patch-kernel-probes3.py <xnu 源码树>
"""
import sys
from pathlib import Path

def probe(row, fb_const, reg):
    return f'''	/* HAKIOS REGDUMP {reg} at row {row} */
	mrs	x12, {reg}
	ldr	x13, ={fb_const}
	mov	x14, #64
	mov	w15, #0xFF0000
	mov	w16, #0x000000
3:	and	w17, w12, #1
	cmp	w17, #0
	csel	w17, w15, w16, ne
	str	w17, [x13], #4
	lsr	x12, x12, #1
	subs	x14, x14, #1
	b.ne	3b
'''

# framebuffer 0xFC800000 + row*1440*4
ROWS = [
    (210, "0xFC927800", "tcr_el1"),
    (212, "0xFC92A200", "sctlr_el1"),
    (214, "0xFC92CF00", "id_aa64mmfr0_el1"),
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
    block = "".join(probe(row, fb, reg) for row, fb, reg in ROWS)
    text = text.replace(ANCHOR, block + ANCHOR, 1)
    src.write_text(text, encoding="utf-8")
    print("patch-probes3: register dumps added (rows 210/212/214)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
