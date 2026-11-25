#!/usr/bin/env python3
"""HAKIOS-NOINIT: stop bsd_init before launchd starts, on a boot argument.

The machine resets at ~57 s with the kernel healthy and the log ending with launchd's ordinary
syscalls; a panic never reaches panic() (the HAKIOS-PANIC-LOG hook stayed silent in gpu115), and
the loader alone survives 663 s.  So the reset is either triggered by userspace, or by the kernel's
own late init (GPU/display/UFS).  This switch separates the two in one boot:

    hakios_noinit=1   -> bsd_init stops right after mountroot, before launching launchd
    (absent)          -> normal boot

Cheap, and it needs only one kernel build for both modes.

Idempotent; exits 0 when already applied, 1 when the anchor is gone.

    python3 ci/patch-kernel-noinit.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-NOINIT"

ANCHOR = '\tbsd_init_kprintf("vfs_mountroot\\n");\n'

BLOCK = (
    "\t/* " + MARK + ": hakios_noinit=1 stops here, before launchd, so a boot can tell\n"
    "\t * whether the ~57 s reset is triggered by userspace or by the kernel itself. */\n"
    "\t{\n"
    "\t\tuint32_t lafa_noinit = 0;\n"
    "\n"
    "\t\tif (PE_parse_boot_argn(\"hakios_noinit\", &lafa_noinit, sizeof(lafa_noinit)) &&\n"
    "\t\t    lafa_noinit != 0) {\n"
    "\t\t\tprintf(\"HAKIOS-NOINIT: mounted, stopping before launchd\\n\");\n"
    "\t\t\tfor (;;) {\n"
    "\t\t\t\tassert_wait_timeout((event_t)&lafa_noinit, THREAD_UNINT, 10,\n"
    "\t\t\t\t    NSEC_PER_SEC);\n"
    "\t\t\t\tthread_block(THREAD_CONTINUE_NULL);\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t}\n"
)


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    f = next(root.rglob("bsd/kern/bsd_init.c"), None)
    if f is None:
        print("patch-noinit: MISS bsd/kern/bsd_init.c")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-noinit: already applied")
        return 0
    if ANCHOR not in text:
        print("patch-noinit: MISS vfs_mountroot anchor")
        return 1
    text = text.replace(ANCHOR, ANCHOR + BLOCK, 1)
    f.write_text(text, encoding="utf-8")
    print("patch-noinit: installed (runtime switch hakios_noinit=1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
