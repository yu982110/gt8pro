#!/usr/bin/env python3
"""第二组内核探针（第 201 行），切细 common_start 到 MMU 打开这一段：

  P3  写完 TTBR1 之后            —— 白
  P4  打开 MMU 之前（VBAR 已设） —— 黄
  P5  打开 MMU 之后              —— 青

配合第一组（蓝=start_first_cpu、绿=common_start），就能判断是死在
MAIR/TLBI、BCM2837 那段寄存器写、还是 MMU 打开本身。

    python3 ci/patch-kernel-probes2.py <xnu 源码树>
"""
import sys
from pathlib import Path

PROBE = '''	/* HAKIOS PROBE2 {idx}: framebuffer row 201, x = {x} */
	ldr	x9, =0xFC91AC00
	add	x9, x9, #{off}
	mov	w10, #{color}
	mov	x11, #64
2:	str	w10, [x9], #4
	subs	x11, x11, #1
	b.ne	2b
'''

ANCHORS = [
    ("\tMSR_TTBR1_EL1_X0\n\n\t// Set up MAIR", "\tMSR_TTBR1_EL1_X0\n",
     3, 0xFFFFFF, 0),          # 白：TTBR1 写完
    ("\t// Enable caches, MMU, ROP and JOP\n\tMOV64   x0, SCTLR_EL1_DEFAULT",
     "\t// Enable caches, MMU, ROP and JOP\n",
     4, 0xFFFF00, 512),        # 黄：MMU 之前
    ("\tMSR_SCTLR_EL1_X0\n\tisb\t\tsy\n",
     "\tMSR_SCTLR_EL1_X0\n\tisb\t\tsy\n",
     5, 0x00FFFF, 1024),       # 青：MMU 之后
]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "osfmk" / "arm64" / "start.s"
    text = src.read_text(encoding="utf-8")
    if "HAKIOS PROBE2" in text:
        print("patch-probes2: already patched")
        return 0
    for anchor, insert_after, idx, color, off in ANCHORS:
        if anchor not in text:
            print(f"patch-probes2: anchor not found: {anchor[:40]!r}", file=sys.stderr)
            return 1
        probe = PROBE.format(idx=idx, x=off // 4, off=off, color=f"0x{color:06X}")
        text = text.replace(anchor, insert_after + probe, 1)
    # 修：关掉 BCM2837 的中断路由——那是树莓派的寄存器，SM8850 上不该写。
    bcm = "#if defined(BCM2837)\n\t// Setup timer interrupt routing; must be done before MMU is enabled"
    if bcm in text:
        text = text.replace(bcm, "#if 0 /* HAKIOS: BCM2837 timer routing is a Raspberry Pi register, not for lafa */\n\t// Setup timer interrupt routing; must be done before MMU is enabled", 1)
        print("patch-probes2: BCM2837 timer routing disabled")

    src.write_text(text, encoding="utf-8")
    print("patch-probes2: P3/P4/P5 inserted (row 201: white / yellow / cyan)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
