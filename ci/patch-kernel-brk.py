#!/usr/bin/env python3
"""让内核在进入 C 代码之前主动抛异常（brk），验证"内核 → loader"的早期异常回路。

在 start.s 的 `ret`（跳回 arm_init）之前插入：

    mov  x9, #0x1234      ; 标记
    msr  far_el1, x9      ; 把标记放进 FAR_EL1
    brk  #0               ; 触发同步异常

hakios 的 LowExceptionVectorBase 被改成跳回 loader（`return slot patched at ...`），
loader 会把 ESR/ELR/FAR 打进日志 —— 于是屏幕上应该出现 early exception 的信息。

    python3 ci/patch-kernel-brk.py <xnu 源码树>
"""
import sys
from pathlib import Path

BLOCK = '''	/* HAKIOS BRK TEST: raise a synchronous exception right before entering C */
	mov	x9, #0x1234
	msr	far_el1, x9
	brk	#0
'''

ANCHOR = "\t// Return to arm_init()\n\tret\n"


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "osfmk" / "arm64" / "start.s"
    text = src.read_text(encoding="utf-8")
    if "HAKIOS BRK TEST" in text:
        print("patch-brk: already patched")
        return 0
    if ANCHOR not in text:
        print("patch-brk: anchor not found", file=sys.stderr)
        return 1
    text = text.replace(ANCHOR, BLOCK + "\n" + ANCHOR, 1)
    src.write_text(text, encoding="utf-8")
    print("patch-brk: brk #0 with FAR=0x1234 inserted before ret to arm_init")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
