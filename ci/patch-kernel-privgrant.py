#!/usr/bin/env python3
"""HAKIOS-LAFA-PRIVGRANT: let root have every privilege.

The errno probe proved xpcproxy's posix_spawn syscall (244) returns EPERM from inside the
kernel, and none of posix_spawn's own EPERM exits fired.  kern_minimac.c's MACF hook
minisandbox_priv_grant() refuses *every* privilege except PRIV_WORK_INTERVAL and a few
entitlement-backed ones, so a priv_check_cred() somewhere inside posix_spawn fails and the
whole syscall returns EPERM - which is why logd and distnoted never start.

This patch grants everything to root (the stub sandbox has no business blocking root's
spawn), and leaves non-root behaviour alone.

Idempotent; exits 0 when already applied, 1 when the anchor is gone.
"""
import pathlib
import sys

MARK = "/* HAKIOS-LAFA-PRIVGRANT: root gets every privilege; the stub sandbox must not block spawn */"
ANCHOR = (
    "minisandbox_priv_grant(kauth_cred_t cred, int priv)\n"
    "{\n"
    "\tstatic int seen[32];\n"
)
INSERT = (
    "\t" + MARK + "\n"
    "\tif (kauth_cred_issuser(cred)) {\n"
    "\t\treturn 0;\n"
    "\t}\n"
)


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-privgrant.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    hits = 0
    for p in root.rglob("kern_minimac.c"):
        text = p.read_text(errors="replace")
        if MARK in text:
            print("patch-privgrant: already applied in", p)
            hits += 1
            continue
        if ANCHOR not in text:
            print("patch-privgrant: MISS anchor in", p)
            continue
        text = text.replace(ANCHOR, ANCHOR + INSERT, 1)
        p.write_text(text)
        print("patch-privgrant: root granted every privilege in", p)
        hits += 1
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
