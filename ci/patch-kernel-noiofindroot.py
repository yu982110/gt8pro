#!/usr/bin/env python3
"""根设备实验：
  1. 跳过 IOFindBSDRoot（IOKit 里没有块设备，它会卡住）；
  2. 把兜底的根设备从 "sd0a" 改成 "md0"（我们塞进去的 HFS+ ramdisk）。

    python3 ci/patch-kernel-noiofindroot.py <xnu 源码树>
"""
import sys
from pathlib import Path

OLD = "\terr = IOFindBSDRoot(rootdevice, sizeof(rootdevice), &rootdev, &flags);\n"
NEW = ("\t/* HAKIOS: skip the IOKit root search for this test */\n"
       "\terr = 1; /* IOFindBSDRoot(rootdevice, sizeof(rootdevice), &rootdev, &flags); */\n")

OLD_FALLBACK = '\t\tstrlcpy(rootdevice, "sd0a", sizeof(rootdevice));\n'
NEW_FALLBACK = ('\t\t/* HAKIOS: the payload carries an HFS+ ramdisk; mount that, not sd0a */\n'
                '\t\tstrlcpy(rootdevice, "md0", sizeof(rootdevice));\n')


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "bsd" / "kern" / "bsd_init.c"
    text = src.read_text(encoding="utf-8")
    if "HAKIOS: skip the IOKit root search" in text:
        print("patch-noiofindroot: already patched")
        return 0
    if OLD not in text:
        print("patch-noiofindroot: IOFindBSDRoot anchor not found", file=sys.stderr)
        return 1
    text = text.replace(OLD, NEW, 1)
    if OLD_FALLBACK in text:
        text = text.replace(OLD_FALLBACK, NEW_FALLBACK, 1)
        print("patch-noiofindroot: fallback rootdevice -> md0")
    else:
        print("patch-noiofindroot: WARNING fallback not found", file=sys.stderr)
    src.write_text(text, encoding="utf-8")
    print("patch-noiofindroot: IOFindBSDRoot skipped, rootdevice = md0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
