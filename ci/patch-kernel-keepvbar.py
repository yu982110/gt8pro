#!/usr/bin/env python3
"""保留 hakios 的早期异常向量：开 MMU 之前不要用内核自己的向量表覆盖 VBAR_EL1。

配合 patch-kernel-brk.py 使用：brk 触发后，异常应当走 LowExceptionVectorBase →
hakios 的 Lloader_early_exception → 跳回 loader，把日志打出来。

    python3 ci/patch-kernel-keepvbar.py <xnu 源码树>
"""
import sys
from pathlib import Path

AND = "\t// Return to arm_init()\n"

BLOCK_OLD = """\tadrp\tx0, EXT(ExceptionVectorsBase)@page\t\t\t// Load exception vectors base address
\tadd\t\tx0, x0, EXT(ExceptionVectorsBase)@pageoff
\tadd\t\tx0, x0, x22\t\t\t\t\t\t\t\t\t// Convert exception vector address to KVA
\tsub\t\tx0, x0, x23
1:
\tMSR_VBAR_EL1_X0
"""

BLOCK_NEW = """\tadrp\tx0, EXT(ExceptionVectorsBase)@page\t\t\t// Load exception vectors base address
\tadd\t\tx0, x0, EXT(ExceptionVectorsBase)@pageoff
\tadd\t\tx0, x0, x22\t\t\t\t\t\t\t\t\t// Convert exception vector address to KVA
\tsub\t\tx0, x0, x23
1:
\t/* HAKIOS: keep the early (physical) vectors so a post-MMU exception can return to the loader */
"""


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "osfmk" / "arm64" / "start.s"
    text = src.read_text(encoding="utf-8")
    if "HAKIOS: keep the early" in text:
        print("patch-keepvbar: already patched")
        return 0
    if BLOCK_OLD not in text:
        print("patch-keepvbar: VBAR block not found", file=sys.stderr)
        return 1
    text = text.replace(BLOCK_OLD, BLOCK_NEW, 1)
    src.write_text(text, encoding="utf-8")
    print("patch-keepvbar: kernel vector install disabled; early vectors stay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
