#!/usr/bin/env python3
"""检查点 48：arm_init 里调用 machine_startup 之前。

分辨两件事：
  - 48 触发 → checkpoint 机制在那一刻仍然可用，问题在 loader_jump 的守卫或 machine_startup 入口；
  - 48 不触发 → loader_stub_slot 在 21 之后被覆盖（栈/BSS）。

    python3 ci/patch-kernel-call48.py <xnu 源码树>
"""
import sys
from pathlib import Path

OLD = "\tmachine_startup(args);\n"
NEW = ("\t/* HAKIOS CALL48: checkpoint right before the call */\n"
       "\tloader_checkpoint(48);\n"
       "\tmachine_startup(args);\n")


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "osfmk" / "arm" / "arm_init.c"
    text = src.read_text(encoding="utf-8")
    if "HAKIOS CALL48" in text:
        print("patch-call48: already patched")
        return 0
    if OLD not in text:
        print("patch-call48: anchor not found", file=sys.stderr)
        return 1
    if "extern void loader_checkpoint" not in text:
        # arm_init.c 里 hakios 已经声明过（见其补丁），这里保险起见再补一次
        text = text.replace("#include <pexpert/pexpert.h>\n",
                            "#include <pexpert/pexpert.h>\nextern void loader_checkpoint(uint64_t n);\n", 1)
    text = text.replace(OLD, NEW, 1)
    src.write_text(text, encoding="utf-8")
    print("patch-call48: loader_checkpoint(48) inserted before machine_startup()")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
