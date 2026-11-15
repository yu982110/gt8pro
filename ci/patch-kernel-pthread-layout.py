#!/usr/bin/env python3
"""HAKIOS-LAFA-PTHREAD: match the userland libpthread's ksyn_waitq_element layout.

The kernel's struct ksyn_waitq_element (bsd/sys/pthread_internal.h) is an opaque
reservation for what libpthread's private struct actually holds.  Our userland is
iOS 16.5, whose libpthread-514.100.2 keeps a trailing `kwe_uth` pointer:

  * __LP64__: 48 bytes in the stock kernel  -> the userland struct is 56 bytes.

The kernel then reads/writes four bytes past what the caller reserved at every
pthread mutex/cond wait, and the system comes apart a few seconds into userspace --
which is exactly the shape of our boot: launchd and the first daemons run, then
everything stops making system calls at ~t=80 s while backboardd sits in ulock_wait
and logd dies with SIGTRAP again and again.

The reference port fixes the same thing the same way (0019-sweet-builtin-pthread.patch).

Idempotent; exits 0 when already applied, 1 when the anchor is gone.
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-PTHREAD"


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-pthread-layout.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    f = next(root.rglob("bsd/sys/pthread_internal.h"), None)
    if f is None:
        print("patch-pthread: MISS bsd/sys/pthread_internal.h")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-pthread: already applied")
        return 0
    anchor = "\tchar opaque[48];\n"
    repl = ("\t/* " + MARK + ": libpthread-514.100.2 keeps the trailing kwe_uth pointer;\n"
            "\t * 48 bytes here made every pthread wait four bytes short of the caller's struct. */\n"
            "\tchar opaque[56];\n")
    if anchor not in text:
        print("patch-pthread: MISS the opaque[48] anchor")
        return 1
    text = text.replace(anchor, repl, 1)
    f.write_text(text, encoding="utf-8")
    print("patch-pthread: ksyn_waitq_element __LP64__ reservation 48 -> 56 bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
