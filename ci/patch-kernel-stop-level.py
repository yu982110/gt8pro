#!/usr/bin/env python3
"""HAKIOS-STOP: spin at an early kernel-init point, chosen by a boot argument.

The noinit test (stop in bsd_init after mountroot) still resets in 55-78 s, and every service the
port starts by hand (display/HID/GPU) has been switched off -- so the killer is somewhere in the
kernel's own early initialisation.  This gives three earlier stops:

    hakios_stop=1   before PE_init_iokit()            (platform expert, drivers)
    hakios_stop=3   before startup_initialize(EARLY_BOOT)
    (hakios_noinit=1 stops later, in bsd_init after mountroot)

Each stop spins in `wfi` for ever; the loader sees the kernel go quiet and the partition keeps the
log, so one boot says how far the kernel must get before the reset starts.

Must run after patch-kernel-livelog-probe.py (it uses loader_log_line and the anchors stay in
place).  Idempotent; exits 0 when already applied, 1 when an anchor is gone.

    python3 ci/patch-kernel-stop-level.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-STOP"

KSTARTUP = "osfmk/kern/startup.c"

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

ANCHORS = [
    ("\tPE_init_iokit();\n", 1),
    ("\tbsd_init();\n", 2),
    ("\tkernel_startup_initialize_upto(STARTUP_SUB_EARLY_BOOT);\n", 3),
]


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    f = root / KSTARTUP
    if not f.exists():
        print("patch-stop: MISS", KSTARTUP)
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-stop: already applied")
        return 0
    n = 0
    for anchor, level in ANCHORS:
        if anchor in text:
            text = text.replace(anchor, (GUARD % (level, level)) + anchor, 1)
            n += 1
        else:
            print("  MISS anchor for level", level)
    f.write_text(text, encoding="utf-8")
    print("patch-stop: installed, %d of %d stops" % (n, len(ANCHORS)))
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
