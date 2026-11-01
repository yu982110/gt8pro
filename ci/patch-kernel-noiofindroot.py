#!/usr/bin/env python3
"""临时让 setconf() 跳过 IOFindBSDRoot（直接按失败处理），验证卡点是否在那里。

    python3 ci/patch-kernel-noiofindroot.py <xnu 源码树>
"""
import sys
from pathlib import Path

OLD = "\terr = IOFindBSDRoot(rootdevice, sizeof(rootdevice), &rootdev, &flags);\n"
NEW = ("\t/* HAKIOS: skip the IOKit root search for this test */\n"
       "\terr = 1; /* IOFindBSDRoot(rootdevice, sizeof(rootdevice), &rootdev, &flags); */\n")


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "bsd" / "kern" / "bsd_init.c"
    text = src.read_text(encoding="utf-8")
    if "HAKIOS: skip the IOKit root search" in text:
        print("patch-noiofindroot: already patched")
        return 0
    if OLD not in text:
        print("patch-noiofindroot: anchor not found", file=sys.stderr)
        return 1
    src.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print("patch-noiofindroot: IOFindBSDRoot skipped (err = 1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
