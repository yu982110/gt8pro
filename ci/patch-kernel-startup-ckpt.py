#!/usr/bin/env python3
"""在 kernel_bootstrap / kernel_bootstrap_thread 的关键步骤插检查点（50-55）。

配合 payload 的 `--checkpoint N`：内核跑到第 N 个检查点就返回 loader。

    python3 ci/patch-kernel-startup-ckpt.py <xnu 源码树>
"""
import sys
from pathlib import Path

DECL = "extern void loader_checkpoint(uint64_t n);\n"

# (锚点整行, 检查点号, 插在锚点前/后)
SITES = [
    ('\tkernel_bootstrap_log("vm_mem_bootstrap");\n', 50, "after"),
    ('\tkernel_bootstrap_log("machine_init");\n', 51, "after"),
    ('\tkernel_bootstrap_log("kernel_thread_create");\n', 52, "after"),
    ('\tkernel_bootstrap_thread_log("sched_startup");\n', 53, "after"),
    ('\tkernel_bootstrap_log("PE_init_iokit");\n', 54, "after"),
    ("\tbsd_init();\n", 55, "before"),
]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "osfmk" / "kern" / "startup.c"
    text = src.read_text(encoding="utf-8")
    if "HAKIOS STARTUP CKPT" in text:
        print("patch-startup-ckpt: already patched")
        return 0

    anchor_decl = "#include <kern/startup.h>\n"
    decl = ("/* HAKIOS STARTUP CKPT: bring-up checkpoints, see osfmk/arm64/loader_return.c */\n"
            + DECL)
    if anchor_decl in text:
        text = text.replace(anchor_decl, anchor_decl + decl, 1)
    else:
        # 退而求其次：放在第一个 #include 之后
        idx = text.find("#include")
        nl = text.find("\n", idx)
        text = text[:nl + 1] + decl + text[nl + 1:]

    done = 0
    for anchor, n, where in SITES:
        if anchor not in text:
            print(f"patch-startup-ckpt: anchor missing for {n}: {anchor.strip()[:50]}", file=sys.stderr)
            continue
        cp = f"\tloader_checkpoint({n});\n"
        if where == "after":
            text = text.replace(anchor, anchor + cp, 1)
        else:
            text = text.replace(anchor, cp + anchor, 1)
        done += 1

    if done == 0:
        print("patch-startup-ckpt: nothing inserted", file=sys.stderr)
        return 1

    src.write_text(text, encoding="utf-8")
    print(f"patch-startup-ckpt: {done}/{len(SITES)} checkpoints (50-55) inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
