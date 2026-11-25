#!/usr/bin/env python3
"""HAKIOS-STOP: spin at an early kernel-init point, chosen by a boot argument.

The reset was narrowed by stopping earlier and earlier:

    hakios_stop=1   before PE_init_iokit()                     -> survives
    hakios_stop=2   before bsd_init()                          -> survives
    hakios_stop=3   before startup_initialize(EARLY_BOOT)      -> survives
    hakios_noinit=1 after bsd_init's vfs_mountroot print       -> resets in 55-78 s

so the killer is inside bsd_init(), between its entry and the vfs_mountroot print.  This adds a
midpoint there:

    hakios_stop=4   after the "calling bsd_bufferinit" print (checkpoint 61)

If 4 survives, the killer is between that step and mountroot; if it resets, it is in the earlier
bsd_init steps.

Each stop spins in `wfi` for ever; the loader notices the quiet kernel, and the partition keeps
the log.  Must run after patch-kernel-livelog-probe.py (loader_log_line is declared there) and
patch-kernel-bsd-ckpt.py (the prints exist).  Idempotent.

    python3 ci/patch-kernel-stop-level.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-STOP"

KSTARTUP = "osfmk/kern/startup.c"
KBSDINIT = "bsd/kern/bsd_init.c"

GUARD = (
    "\t{\n"
    "\t\tuint32_t lafa_stop = 0;\n"
    "\n"
    "\t\tif (PE_parse_boot_argn(\"hakios_stop\", &lafa_stop, sizeof(lafa_stop)) &&\n"
    "\t\t    lafa_stop == %d) {\n"
    "\t\t\tloader_log_line(\"HAKIOS-STOP: level %d, spinning\\n\");\n"
    "\t\t\tfor (;;) {\n"
    "\t\t\t\t__asm__ volatile(\"wfi\");\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t}\n"
)

STARTUP_ANCHORS = [
    ("\tPE_init_iokit();\n", 1),
    ("\tbsd_init();\n", 2),
    ("\tkernel_startup_initialize_upto(STARTUP_SUB_EARLY_BOOT);\n", 3),
]

BSDINIT_ANCHORS = [
    ('\tbsd_init_kprintf("calling kmem_suballoc\\n");\n', 6),
    ('\tbsd_init_kprintf("calling bsd_bufferinit\\n");\n', 4),
]


def apply(f: pathlib.Path, anchors, after: bool) -> int:
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-stop: %s already has a stop" % f.name)
        return 0
    n = 0
    for anchor, level in anchors:
        if anchor in text:
            g = GUARD % (level, level)
            text = text.replace(anchor, (anchor + g) if after else (g + anchor), 1)
            n += 1
        else:
            print("  MISS anchor for level", level, "in", f.name)
    if n:
        f.write_text(text, encoding="utf-8")
    return n


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    total = 0
    su = root / KSTARTUP
    bi = root / KBSDINIT
    if su.exists():
        total += apply(su, STARTUP_ANCHORS, after=False)
    else:
        print("patch-stop: MISS", KSTARTUP)
    if bi.exists():
        total += apply(bi, BSDINIT_ANCHORS, after=True)
    else:
        print("patch-stop: MISS", KBSDINIT)
    print("patch-stop: installed, %d stop(s)" % total)
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
