#!/usr/bin/env python3
"""patch-kernel-spawndriver.py -- let launchd spawn a job whose spawnattr says DRIVER.

Why
---
With the boot-task chain finally running (launchd.darwin 0x041408 NOPed), launchd stays up and
starts spawning jobs -- and one of them dies in a loop:

    HAKIOS-EXEC: EPERM site1 (spawnattr DRIVER entitlement)
    HAKIOS-NOLOGD: logd spawn refused
    pid 57/58/59... (a new pid every second)

That probe (ci/patch-kernel-execprobe.py) sits in front of posix_spawn's EPERM in
bsd/kern/kern_exec.c:

    if (proctype == POSIX_SPAWN_PROC_TYPE_DRIVER) {
        if (!IOCurrentTaskHasEntitlement(POSIX_SPAWN_ENTITLEMENT_DRIVER)) {
            error = EPERM;

The caller is launchd, which does carry com.apple.private.spawn-driver, but our miniamfi does not
surface it for that task, so the entitlement query answers false.  This port already runs launchd
and every payload binary without AMFI enforcement, so the check is made to never deny: the same
course as the other entitlement sites in this tree.

The replacement is a text edit of the condition itself (not of the surrounding lines), so it
applies in either order relative to the execprobe patch, and the probe line stays where it is.

    python3 ci/patch-kernel-spawndriver.py <xnu tree>
"""
import pathlib
import sys

MARK = "/* HAKIOS-LAFA-SPAWNDRIVER */"
FROM = "\t\t\tif (!IOCurrentTaskHasEntitlement(POSIX_SPAWN_ENTITLEMENT_DRIVER)) {\n"
TO = ("\t\t\t" + MARK + ": the caller's entitlement is not visible through miniamfi here,\n"
      "\t\t\t * and this port runs without AMFI enforcement, so allow it. */\n"
      "\t\t\tif (0 && !IOCurrentTaskHasEntitlement(POSIX_SPAWN_ENTITLEMENT_DRIVER)) {\n")


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-spawndriver.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    target = next(root.rglob("bsd/kern/kern_exec.c"), None)
    if target is None:
        print("patch-spawndriver: MISS bsd/kern/kern_exec.c")
        return 1
    text = target.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-spawndriver: already applied")
        return 0
    # The same condition guards more than one exit in this file (return EPERM in
    # exec_validate_spawnattr_policy, error = EPERM in posix_spawn), and the older
    # patch-kernel-spawnpolicy.py anchors on the return form only, which is why it MISSed and the
    # loop kept going.  Rewriting the condition itself catches every copy.
    n = text.count(FROM)
    if n < 1:
        print("patch-spawndriver: MISS the DRIVER entitlement check (0 matches)")
        return 1
    target.write_text(text.replace(FROM, TO), errors="surrogateescape")
    print("patch-spawndriver: %d DRIVER entitlement check(s) allowed" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
