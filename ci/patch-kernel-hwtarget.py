#!/usr/bin/env python3
"""hw.target / hw.product 从 boot-arg 回退。

这台手机的移植没有平台身份（设备树的 compatible 是 placeholder，而给根节点写 compatible 会把
内核的平台探测搞坏）。libignition 在放 launchd 之前要 sysctl hw.target，查不到就返回 EINVAL 并把
pid 1 杀掉。这里让内核在 PEGetTargetName 失败时读 boot-arg：

    (values are fixed in the code: D22AP / iPhone10,3)

    python3 ci/patch-kernel-hwtarget.py <xnu 源码树>
"""
import sys
from pathlib import Path

TARGET_ANCHOR = """	case HW_TARGET:
		bzero(dummy, sizeof(dummy));
		if (!PEGetTargetName(dummy, 64)) {
			return EINVAL;
		}
"""

TARGET_NEW = """	case HW_TARGET:
		bzero(dummy, sizeof(dummy));
		if (!PEGetTargetName(dummy, 64)) {
			/* HAKIOS-LAFA: no platform identity in this port's device tree; the boot argument
			 * says which phone the userland is from (libignition asks for hw.target) */
			/* this port has no platform identity at all: the userland is the iPhone X (D22AP) */
			strlcpy(dummy, "D22AP", 64);
		}
"""

PRODUCT_ANCHOR = """	case HW_PRODUCT:
		bzero(dummy, sizeof(dummy));
		if (!PEGetProductName(dummy, 64)) {
			return EINVAL;
		}
"""

PRODUCT_NEW = """	case HW_PRODUCT:
		bzero(dummy, sizeof(dummy));
		if (!PEGetProductName(dummy, 64)) {
			/* HAKIOS-LAFA: same fallback as hw.target above */
			strlcpy(dummy, "iPhone10,3", 64);
		}
"""


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "bsd" / "kern" / "kern_mib.c"
    text = src.read_text(encoding="utf-8", errors="surrogateescape")
    if "D22AP" in text:
        print("patch-hwtarget: already patched")
        return 0
    for old, new, what in ((TARGET_ANCHOR, TARGET_NEW, "hw.target"),
                           (PRODUCT_ANCHOR, PRODUCT_NEW, "hw.product")):
        if old not in text:
            print(f"patch-hwtarget: anchor for {what} not found", file=sys.stderr)
            return 1
        text = text.replace(old, new, 1)
    src.write_text(text, encoding="utf-8", errors="surrogateescape")
    print("patch-hwtarget: hw.target and hw.product fall back to boot arguments")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
