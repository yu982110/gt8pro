#!/usr/bin/env python3
"""bsd_init 里 63→64 之间的细分检查点（68-71）。

  68  loopattach() 之后
  69  ether_family_init() 之后
  70  net_init_run() 之后
  71  inittodr(0) 之后

    python3 ci/patch-kernel-bsd2-ckpt.py <xnu 源码树>
"""
import sys
from pathlib import Path

SITES = [
    ("\tloopattach();                   /* XXX */\n", 68),
    ("\tether_family_init();\n", 69),
    ("\tnet_init_run();\n", 70),
    ("\tinittodr(0);\n", 71),
]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "bsd" / "kern" / "bsd_init.c"
    text = src.read_text(encoding="utf-8")
    if "loader_checkpoint(68)" in text:
        print("patch-bsd2: already patched")
        return 0
    done = 0
    for anchor, n in SITES:
        if anchor not in text:
            print(f"patch-bsd2: anchor missing for {n}: {anchor.strip()[:40]}", file=sys.stderr)
            continue
        text = text.replace(anchor, anchor + f"\tloader_checkpoint({n});\n", 1)
        done += 1
    if done == 0:
        print("patch-bsd2: nothing inserted", file=sys.stderr)
        return 1
    src.write_text(text, encoding="utf-8")
    print(f"patch-bsd2: {done}/{len(SITES)} checkpoints (68-71) inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
