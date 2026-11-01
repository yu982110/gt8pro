#!/usr/bin/env python3
"""md 设备诊断：打印 RAMDisk / RAMDisk1 两个设备树属性是否存在，以及 mdevadd 的结果。

下次上机就不用再猜 /dev/md1 为什么没建出来了。

    python3 ci/patch-kernel-mdtrace.py <xnu 源码树>
"""
import sys
from pathlib import Path

RD0 = 'data = (OSData *)regEntry->getProperty("RAMDisk");'
RD1 = 'data = (OSData *)regEntry->getProperty("RAMDisk1");'
MDEV = "(void)mdevadd(-1, ml_static_ptovirt(ramdParms[0]) >> 12, (unsigned int) (ramdParms[1] >> 12), 0);"

RD0_PRINT = RD0 + '\n\t\t\tprintf("lafa-md: RAMDisk %spresent\\n", data ? "" : "NOT ");'
RD1_PRINT = RD1 + '\n\t\t\tprintf("lafa-md: RAMDisk1 %spresent\\n", data ? "" : "NOT ");'

MDEV_NEW = """dev_t lafa_md1 = mdevadd(-1, ml_static_ptovirt(ramdParms[0]) >> 12, (unsigned int) (ramdParms[1] >> 12), 0);
					printf("lafa-md: RAMDisk1 pa %llx size %llx -> md %d (major %d minor %d)\\n",
					    (unsigned long long)ramdParms[0], (unsigned long long)ramdParms[1],
					    (int)lafa_md1, (int)major(lafa_md1), (int)minor(lafa_md1));"""


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "iokit" / "bsddev" / "IOKitBSDInit.cpp"
    text = src.read_text(encoding="utf-8", errors="surrogateescape")
    if "lafa-md: RAMDisk1" in text:
        print("patch-mdtrace: already patched")
        return 0
    if RD1 not in text:
        print("patch-mdtrace: RAMDisk1 anchor not found", file=sys.stderr)
        return 1
    if RD0 in text:
        text = text.replace(RD0, RD0_PRINT, 1)
    text = text.replace(RD1, RD1_PRINT, 1)
    # 只改 RAMDisk1 那段里的 mdevadd（它出现在 RAMDisk1 之后）
    idx = text.find(RD1_PRINT)
    pos = text.find(MDEV, idx)
    if pos < 0:
        print("patch-mdtrace: mdevadd for RAMDisk1 not found", file=sys.stderr)
        return 1
    text = text[:pos] + MDEV_NEW + text[pos + len(MDEV):]
    src.write_text(text, encoding="utf-8", errors="surrogateescape")
    print("patch-mdtrace: RAMDisk/RAMDisk1 trace inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
