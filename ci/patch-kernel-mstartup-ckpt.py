#!/usr/bin/env python3
"""machine_startup / kernel_bootstrap 入口的检查点（45/46/47）。

  45  machine_startup 入口（machine_conf 之前）
  46  machine_conf() 之后
  47  kernel_bootstrap 入口（vm_mem_bootstrap 之前）

    python3 ci/patch-kernel-mstartup-ckpt.py <xnu 源码树>
"""
import sys
from pathlib import Path

DECL = "extern void loader_checkpoint(uint64_t n);\n"

MR_OLD = "\tmachine_conf();\n\n\n\t/*\n\t * Kick off the kernel bootstrap.\n\t */\n\tkernel_bootstrap();\n"
MR_NEW = ("\t/* HAKIOS MSTARTUP CKPT */\n"
          "\tloader_checkpoint(45);\n"
          "\tmachine_conf();\n"
          "\tloader_checkpoint(46);\n\n\n"
          "\t/*\n\t * Kick off the kernel bootstrap.\n\t */\n\tkernel_bootstrap();\n")

KB_OLD = '\tkernel_bootstrap_log("vm_mem_bootstrap");\n'
KB_NEW = '\tloader_checkpoint(47);\n' + KB_OLD

# 函数体第一行（HAS_IPI 块之前）
MS_OLD = "machine_startup(__unused boot_args * args)\n{\n"
MS_NEW = ("machine_startup(__unused boot_args * args)\n{\n"
          "\tloader_checkpoint(49); /* HAKIOS MSTARTUP CKPT: first line */\n")


def add_decl(text: str) -> str:
    if DECL in text:
        return text
    i = text.find("#include")
    nl = text.find("\n", i)
    return text[:nl + 1] + "/* HAKIOS MSTARTUP CKPT */\n" + DECL + text[nl + 1:]


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    mr = root / "osfmk" / "arm64" / "machine_routines.c"
    kb = root / "osfmk" / "kern" / "startup.c"

    mtext = mr.read_text(encoding="utf-8")
    if "HAKIOS MSTARTUP CKPT" in mtext:
        print("patch-mstartup: already patched")
        return 0
    if MR_OLD not in mtext:
        print("patch-mstartup: machine_startup anchor not found", file=sys.stderr)
        return 1
    mtext = add_decl(mtext).replace(MR_OLD, MR_NEW, 1)
    if MS_OLD in mtext:
        mtext = mtext.replace(MS_OLD, MS_NEW, 1)
    else:
        print("patch-mstartup: machine_startup body anchor not found", file=sys.stderr)
    mr.write_text(mtext, encoding="utf-8")

    ktext = kb.read_text(encoding="utf-8")
    if KB_OLD not in ktext:
        print("patch-mstartup: kernel_bootstrap anchor not found", file=sys.stderr)
        return 1
    ktext = add_decl(ktext).replace(KB_OLD, KB_NEW, 1)
    kb.write_text(ktext, encoding="utf-8")

    print("patch-mstartup: MR=%s KB=%s MS49=%s" % (MR_OLD not in mtext, KB_OLD not in ktext, "49" in mtext))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
