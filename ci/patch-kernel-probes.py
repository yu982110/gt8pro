#!/usr/bin/env python3
"""内核早期执行探针：在 XNU 的 arm64 启动路径上，往 framebuffer 画色块。

四个点（y=200 这一行，避开 loader 自己涂的红色块）：
  P0  start_first_cpu   —— 红
  P1  common_start      —— 绿
  P2  arm_init_tramp    —— 蓝
  P3  （由 arm_init 的 C 代码调，见 patch-kernel-probe-c.py）

看到几个色块，就知道内核走到第几步；一个都没有 = 连 `b start_first_cpu` 都没执行。

    python3 ci/patch-kernel-probes.py <xnu 源码树>
"""
import sys
from pathlib import Path

# ldr x9, =0xFC919400  -> framebuffer + 200 行 (200*1440*4 = 0x119400)
PROBE = '''	/* HAKIOS PROBE {idx}: framebuffer row 200, x = {x} */
	ldr	x9, =0xFC919400
	add	x9, x9, #{off}
	mov	w10, #{color}
	mov	x11, #64
1:	str	w10, [x9], #4
	subs	x11, x11, #1
	b.ne	1b
'''

ANCHORS = [
    ("LEXT(start_first_cpu)\n", 0, 0x0000FF, 0),      # 0xFF0000 is (BGR?) keep simple: use 0x0000FF blue-ish
    ("common_start:\n", 1, 0x00FF00, 512),
    ("arm_init_tramp:\n", 2, 0xFF0000, 1024),
]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "osfmk" / "arm64" / "start.s"
    text = src.read_text(encoding="utf-8")
    if "HAKIOS PROBE" in text:
        print("patch-probes: already patched")
        return 0
    for anchor, idx, color, off in ANCHORS:
        if anchor not in text:
            print(f"patch-probes: anchor not found: {anchor!r}", file=sys.stderr)
            return 1
        probe = PROBE.format(idx=idx, x=off // 4, off=off, color=f"0x{color:06X}")
        text = text.replace(anchor, anchor + probe, 1)
    src.write_text(text, encoding="utf-8")
    print("patch-probes: three asm probes inserted (start_first_cpu, common_start, arm_init_tramp)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
