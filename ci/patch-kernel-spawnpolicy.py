#!/usr/bin/env python3
"""HAKIOS-LAFA-SPAWN: let a spawner use POSIX_SPAWN_PROC_TYPE_DRIVER without the entitlement.

The exec probe proved the last blocker: launchd/xpcproxy spawn system jobs with
POSIX_SPAWN_PROC_TYPE_DRIVER, and exec_validate_spawnattr_policy() rejects that with
EPERM unless the spawner holds com.apple.private.spawn-driver.  This kernel has no
entitlement model, so every system daemon spawned through xpcproxy (logd, distnoted, ...)
died with `posix_spawn(...), error 0xd`.

This patch removes that one check; the RING probe line stays as evidence.

Idempotent; exits 0 when already applied, 1 when the anchor is gone.
"""
import pathlib
import sys

MARK = "/* HAKIOS-LAFA-SPAWN: the DRIVER spawn type is allowed without the entitlement */"
ANCHOR = (
    "\t\tif (proctype == POSIX_SPAWN_PROC_TYPE_DRIVER) {\n"
    "\t\t\tif (!IOCurrentTaskHasEntitlement(POSIX_SPAWN_ENTITLEMENT_DRIVER)) {\n"
    "\t\t\t\treturn EPERM;\n"
    "\t\t\t}\n"
    "\t\t}\n"
)


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-spawnpolicy.py <xnu-tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    f = root / "bsd/kern/kern_exec.c"
    if not f.exists():
        print("patch-spawnpolicy: MISS bsd/kern/kern_exec.c")
        return 1
    text = f.read_text(errors="replace")
    if MARK in text:
        print("patch-spawnpolicy: already applied")
        return 0
    if ANCHOR not in text:
        print("patch-spawnpolicy: MISS anchor")
        return 1
    repl = (
        MARK + "\n"
        "\t\tif (proctype == POSIX_SPAWN_PROC_TYPE_DRIVER) {\n"
        "\t\t\t/* allowed: this kernel does not model spawn-driver */\n"
        "\t\t}\n"
    )
    text = text.replace(ANCHOR, repl, 1)
    f.write_text(text)
    print("patch-spawnpolicy: DRIVER spawn type allowed without entitlement")
    return 0


if __name__ == "__main__":
    sys.exit(main())
