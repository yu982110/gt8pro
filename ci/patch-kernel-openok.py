#!/usr/bin/env python3
"""HAKIOS-LAFA-OPENOK: a spawn file action that cannot open the console is not fatal.

The HAKIOS-FACT probe proved the failure: PSFA_OPEN of fd 1 (stdout) fails with EPERM,
because this port's devfs cannot open the console node yet, and POSIX considers every
file-action failure fatal, so the whole posix_spawn syscall dies -> logd and distnoted
never start.

This patch skips a failed PSFA_OPEN instead of aborting the spawn; the child simply runs
without that descriptor.  The HAKIOS-FACT line stays for evidence.

Idempotent; exits 0 when already applied, 1 when the anchor is gone.
"""
import pathlib
import sys

MARK = "/* HAKIOS-LAFA-OPENOK */"
ANCHOR = (
    "\t\t/* All file actions failures are considered fatal, per POSIX */\n"
    "\n"
    "\t\tif (error) {\n"
)
INSERT = (
    "\t\t\t" + MARK[:-2] + ": the console node cannot be opened yet; skip the action */\n"
    "\t\t\tif (PSFA_OPEN == psfa->psfaa_type) {\n"
    "\t\t\t\terror = 0;\n"
    "\t\t\t\tcontinue;\n"
    "\t\t\t}\n"
)


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-openok.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    hits = 0
    for p in root.rglob("kern_exec.c"):
        text = p.read_text(errors="replace")
        if MARK in text:
            print("patch-openok: already applied in", p)
            hits += 1
            continue
        if ANCHOR not in text:
            print("patch-openok: MISS anchor in", p)
            continue
        text = text.replace(ANCHOR, ANCHOR + INSERT, 1)
        p.write_text(text)
        print("patch-openok: failed PSFA_OPEN no longer aborts posix_spawn in", p)
        hits += 1
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
