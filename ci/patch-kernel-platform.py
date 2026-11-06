#!/usr/bin/env python3
"""给这个移植一个平台身份：hw.target = D22AP。

本移植的设备树没有 Apple 的平台身份（compatible 是 placeholder，往根节点补 compatible/model
会把内核的平台探测搞坏：内核一行日志都不出）。而 libignition 在放 launchd 之前要
sysctl hw.target，查不到就返回 EINVAL 并把 pid 1 杀掉，于是 panic。

所以改在最底下的地方：IODTPlatformExpert::getTargetName / getProductName 的 arm64 分支，
它们本来读设备树；这里直接回答这台手机该有的名字。system.apfs 是 iPhone X（D22AP）的。

    python3 ci/patch-kernel-platform.py <xnu 源码树>
"""
import sys
from pathlib import Path

TARGET_OLD = """#else
	return getModelName( name, maxLength );
#endif
}

/* Examples: iMacPro1,1, iPhone11,6... */
"""

TARGET_NEW = """#else
	/* HAKIOS-LAFA: no platform identity in this port's device tree, and adding one there stops
	 * the kernel from booting. The userland is the iPhone X (D22AP); libignition asks for
	 * hw.target before it lets launchd go on. */
	if (maxLength < 6) {
		return false;
	}
	strlcpy( name, "D22AP", maxLength );
	return true;
#endif
}

/* Examples: iMacPro1,1, iPhone11,6... */
"""

PRODUCT_OLD = """#else
	return getMachineName( name, maxLength );
#endif
}
"""

PRODUCT_NEW = """#else
	/* HAKIOS-LAFA: same as getTargetName above */
	if (maxLength < 11) {
		return false;
	}
	strlcpy( name, "iPhone10,3", maxLength );
	return true;
#endif
}
"""


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "iokit" / "Kernel" / "IOPlatformExpert.cpp"
    text = src.read_text(encoding="utf-8", errors="surrogateescape")
    if "HAKIOS-LAFA: no platform identity" in text:
        print("patch-platform: already patched")
        return 0
    if TARGET_OLD not in text:
        print("patch-platform: getTargetName anchor not found", file=sys.stderr)
        return 1
    text = text.replace(TARGET_OLD, TARGET_NEW, 1)
    if PRODUCT_OLD in text:
        text = text.replace(PRODUCT_OLD, PRODUCT_NEW, 1)
    else:
        print("patch-platform: getProductName anchor not found (continuing)", file=sys.stderr)
    src.write_text(text, encoding="utf-8", errors="surrogateescape")
    print("patch-platform: hw.target = D22AP, hw.product = iPhone10,3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
