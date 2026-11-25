#!/usr/bin/env python3
"""HAKIOS-LAFA-UFSHCI-NOPROOF: do not refuse the controller over UEFI's last command.

hakios.ufshc.c takes the controller over only when UEFI's slot 0 holds a read it understands:

    return hc_refusal(upiu, *dw0, 0xff, true) == NULL ? NULL : "UEFI's slot 0 does not hold a read";

On this phone the loader writes its own log to the partition right up to the handoff, so UEFI's
last command is a SYNCHRONIZE CACHE (CDB 0x35), which the whitelist refuses -- and the kernel then
says "ufs: native: not used" and reads every block through the loader.  That is what dies 60-85 s
into a run ("no answer from the loader") and what keeps the flash root unreadable in the stable
(native) configuration.

The slot-0 record is only informational (the take-back path prints it); the switch-in proves the
controller with its own TEST UNIT READY on its own list (hc_prove), and every command this driver
issues is whitelisted anyway.  Keep the record, drop the refusal.

    python3 ci/patch-kernel-ufshc-noproof.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-UFSHCI-NOPROOF"
OLD = '\treturn hc_refusal(upiu, *dw0, 0xff, true) == NULL ? NULL : "UEFI\'s slot 0 does not hold a read";'
NEW = ('\t/* %s: UEFI\'s last command is only recorded, not a gate -- our own TUR proves the\n'
       '\t * controller once the list is ours.  The loader leaves a SYNCHRONIZE CACHE there. */\n'
       '\t(void)hc_refusal(upiu, *dw0, 0xff, true);\n'
       '\treturn NULL;') % MARK


def find_driver(root: pathlib.Path):
    for cand in (
        root.parent.parent.parent / "drivers/storage/hakios.ufshc.c",
        root.parent.parent / "drivers/storage/hakios.ufshc.c",
    ):
        if cand.exists():
            return cand
    return next(root.rglob("hakios.ufshc.c"), None)


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    f = find_driver(root)
    if f is None:
        print("patch-ufshc-noproof: MISS hakios.ufshc.c")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-ufshc-noproof: already applied")
        return 0
    if OLD not in text:
        print("patch-ufshc-noproof: MISS slot-0 proof in %s" % f)
        return 1
    text = text.replace(OLD, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-ufshc-noproof: the switch-in no longer needs a read in UEFI's slot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
