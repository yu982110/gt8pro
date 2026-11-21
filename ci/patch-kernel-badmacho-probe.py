#!/usr/bin/env python3
"""Say which check rejected the Mach-O, instead of guessing from errno 8.

load_init_program reports `failed loading /sbin/launchd: errno 8` (ENOEXEC), and 0.18 s pass
between "attempting to load" and the failure -- so the file is read and then rejected by
load_machfile.  ENOEXEC comes from load_return_to_errno(LOAD_BADMACHO), and LOAD_BADMACHO is
returned from a good many places in bsd/kern/mach_loader.c.  kprintf works in this build
(the log has "kprintf initialized" and every bsd_init step), so the cheapest way to the answer
is to print __LINE__ at each of those returns and read the log afterwards.

    python3 ci/patch-kernel-badmacho-probe.py <xnu tree>

Idempotent (MARK). It rewrites `return LOAD_BADMACHO;` wherever it appears, so it needs no
context anchors and cannot silently miss one.

Note this is a *probe*: it changes no control flow, and the printf goes through the port's
ring (patch-kernel-noserial takes the UART out of serial_putc_crlf).
"""
import pathlib
import re
import sys

MARK = "HAKIOS-BADMACHO-PROBE"


def main(root):
    p = pathlib.Path(root) / "bsd/kern/mach_loader.c"
    if not p.exists():
        print("patch-badmacho-probe: %s not found" % p)
        return 1
    t = p.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-badmacho-probe: already applied")
        return 0
    n_badmacho = t.count("return LOAD_BADMACHO;")
    n_badarch = t.count("return LOAD_BADARCH;")
    if n_badmacho == 0:
        print("patch-badmacho-probe: no `return LOAD_BADMACHO;` found")
        return 1
    # ENOEXEC <- LOAD_BADMACHO; print the source line, then return exactly as before.
    # (no % formatting here: the replacement itself contains %s and %d)
    badmacho = '{ printf("[' + MARK + '] HAKIOS-BADMACHO:%d\\n", __func__, __LINE__); return LOAD_BADMACHO; }'
    badarch = '{ printf("[' + MARK + '] HAKIOS-BADARCH:%d\\n", __func__, __LINE__); return LOAD_BADARCH; }'
    t = t.replace("return LOAD_BADMACHO;", badmacho)
    t = t.replace("return LOAD_BADARCH;", badarch)
    p.write_text(t, errors="surrogateescape")
    print("patch-badmacho-probe: %d LOAD_BADMACHO + %d LOAD_BADARCH returns now print their line"
          % (n_badmacho, n_badarch))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
