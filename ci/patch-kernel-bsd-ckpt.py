#!/usr/bin/env python3
"""在 bsd_init() 的关键阶段插检查点（60-67）。

    python3 ci/patch-kernel-bsd-ckpt.py <xnu 源码树>
"""
import sys
from pathlib import Path

DECL = "extern void loader_checkpoint(uint64_t n);\n"

SITES = [
    ('\tbsd_init_kprintf("calling dev_kmem_init\\n");\n', 60),
    ('\tbsd_init_kprintf("calling bsd_bufferinit\\n");\n', 61),
    ('\tbsd_init_kprintf("calling vfsinit\\n");\n', 62),
    ('\tbsd_init_kprintf("calling bsd_autoconf\\n");\n', 63),
    ('\tbsd_init_kprintf("vfs_mountroot\\n");\n', 64),
    ('\tbsd_init_kprintf("calling siginit\\n");\n', 65),
    ('\tbsd_init_kprintf("calling bsd_utaskbootstrap\\n");\n', 66),
    ('\tbsd_init_kprintf("done\\n");\n', 67),
]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "bsd" / "kern" / "bsd_init.c"
    text = src.read_text(encoding="utf-8")
    if "HAKIOS BSD CKPT" in text:
        print("patch-bsd-ckpt: already patched")
        return 0

    anchor = '#include <sys/bsd_init.h>\n'
    decl = "/* HAKIOS BSD CKPT: bring-up checkpoints */\n" + DECL
    if anchor in text:
        text = text.replace(anchor, anchor + decl, 1)
    else:
        i = text.find("#include")
        nl = text.find("\n", i)
        text = text[:nl + 1] + decl + text[nl + 1:]

    done = 0
    for anchor, n in SITES:
        if anchor not in text:
            print(f"patch-bsd-ckpt: anchor missing for {n}", file=sys.stderr)
            continue
        text = text.replace(anchor, anchor + f"\tloader_checkpoint({n});\n", 1)
        done += 1

    if done == 0:
        print("patch-bsd-ckpt: nothing inserted", file=sys.stderr)
        return 1
    src.write_text(text, encoding="utf-8")
    print(f"patch-bsd-ckpt: {done}/{len(SITES)} checkpoints (60-67) inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
