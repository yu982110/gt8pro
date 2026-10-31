#!/usr/bin/env python3
"""第二组内核探针（第 201 行）——只用**唯一的注释行**做锚点，避免插进宏定义里。

  P3  设 MAIR 之前            —— 白
  P4  打开 MMU 之前           —— 黄

另外关掉 BCM2837 的树莓派中断路由（那是 Raspberry Pi 的寄存器，SM8850 上不该写）。

    python3 ci/patch-kernel-probes2.py <xnu 源码树>
"""
import sys
from pathlib import Path

PROBE = '''	/* HAKIOS PROBE2 {idx} */
	ldr	x9, =0xFC91AC00
	add	x9, x9, #{off}
	mov	w10, #{color}
	mov	x11, #64
2:	str	w10, [x9], #4
	subs	x11, x11, #1
	b.ne	2b
'''

# (锚点注释, 探针序号, 颜色, 偏移)
ANCHORS = [
    ("\t// Set up MAIR attr0 for normal memory, attr1 for device memory\n", 3, 0xFFFFFF, 0),
    ("\t// Enable caches, MMU, ROP and JOP\n", 4, 0xFFFF00, 512),
]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "osfmk" / "arm64" / "start.s"
    text = src.read_text(encoding="utf-8")
    if "HAKIOS PROBE2" in text:
        print("patch-probes2: already patched")
        return 0

    for anchor, idx, color, off in ANCHORS:
        if anchor not in text:
            print(f"patch-probes2: anchor not found: {anchor.strip()!r}", file=sys.stderr)
            return 1
        probe = PROBE.format(idx=idx, off=off, color=f"0x{color:06X}")
        text = text.replace(anchor, probe + anchor, 1)

    bcm = "#if defined(BCM2837)\n\t// Setup timer interrupt routing; must be done before MMU is enabled"
    if bcm in text:
        text = text.replace(
            bcm,
            "#if 0 /* HAKIOS: BCM2837 timer routing is a Raspberry Pi register, not for lafa */\n"
            "\t// Setup timer interrupt routing; must be done before MMU is enabled",
            1,
        )
        print("patch-probes2: BCM2837 timer routing disabled")

    src.write_text(text, encoding="utf-8")
    print("patch-probes2: P3/P4 inserted (row 201: white / yellow)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
