#!/usr/bin/env python3
"""hw.target 成功时 panic 一次：用一次启动证明 sysctl 通路和日志通路都是好的。

诊断结论：改了 hw.target 之后"内核零日志"，最可能是**没有 panic**（pid 1 没退出），
而不是内核不写日志 —— 日志 ring 只在 panic/复位时被 loader 读到。

所以这里在 sysctl hw.target 成功返回之前 panic，panic 文本带上取到的值。

    python3 ci/patch-kernel-hwmark.py <xnu 源码树>
"""
import sys
from pathlib import Path

ANCHOR = """	case HW_TARGET:
		bzero(dummy, sizeof(dummy));
		if (!PEGetTargetName(dummy, 64)) {
			return EINVAL;
		}
"""

NEW = """	case HW_TARGET:
		bzero(dummy, sizeof(dummy));
		if (!PEGetTargetName(dummy, 64)) {
			return EINVAL;
		}
		/* HAKIOS-LAFA: one boot that proves the whole path: the value comes out of the platform,
		 * the panic proves the log reaches the loader. Removed once it has been seen. */
		panic("DBG|hwtarget ok: %s", dummy);
"""


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "bsd" / "kern" / "kern_mib.c"
    text = src.read_text(encoding="utf-8", errors="surrogateescape")
    if "DBG|hwtarget ok" in text:
        print("patch-hwmark: already patched")
        return 0
    if ANCHOR not in text:
        print("patch-hwmark: anchor not found", file=sys.stderr)
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    src.write_text(text, encoding="utf-8", errors="surrogateescape")
    print("patch-hwmark: hw.target success panics once")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
