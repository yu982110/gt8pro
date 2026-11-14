#!/usr/bin/env python3
"""HAKIOS-LAFA-EXECPROBE: name the posix_spawn failure site in the ring.

logd and distnoted die with `posix_spawn(...), error 0xd` (EPERM) while every other
binary in the same directories spawns fine.  posix_spawn has four EPERM exits; this
patch puts a loader_log_line in front of each so one boot says which one fires.

Idempotent; exits 0 when nothing matched (the build keeps going, the probe is just absent).
"""
import pathlib
import sys

MARK = "/* HAKIOS-LAFA-EXECPROBE */"
SITES = [
    # (anchor line that precedes the EPERM, probe text)
    ("\t\tif (proctype == POSIX_SPAWN_PROC_TYPE_DRIVER) {\n"
     "\t\t\tif (!IOCurrentTaskHasEntitlement(POSIX_SPAWN_ENTITLEMENT_DRIVER)) {\n",
     "HAKIOS-EXEC: EPERM site1 (spawnattr DRIVER entitlement)\\n"),
    ("\tif (!IOCurrentTaskHasEntitlement( PERSONA_MGMT_ENTITLEMENT)) {\n",
     "HAKIOS-EXEC: EPERM site2 (persona mgmt entitlement)\\n"),
    ("\t\t\tif (!kauth_cred_issuser(kauth_cred_get())) {\n"
     "\t\t\t\terror = EPERM;\n",
     "HAKIOS-EXEC: EPERM site3 (posix_cred_info needs root)\\n"),
    ("\t\t\t\t\tcoal_dbg(\"ERROR: %d not in privilegd \"\n",
     "HAKIOS-EXEC: EPERM site4 (coalition spawn entitlement)\\n"),
]

PROBE = '\t\tloader_log_line("%s");\n' % "{MSG}"


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-execprobe.py <kernel-tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    f = root / "bsd/kern/kern_exec.c"
    if not f.exists():
        print("patch-execprobe: MISS bsd/kern/kern_exec.c")
        return 1
    text = f.read_text(errors="replace")
    if MARK in text:
        print("patch-execprobe: already applied")
        return 0
    added = 0
    for anchor, msg in SITES:
        i = text.find(anchor)
        if i < 0:
            print("patch-execprobe: MISS anchor for", msg)
            continue
        line_start = text.rfind("\n", 0, i) + 1
        text = text[:line_start] + '\t\t' + MARK + "\n" + PROBE.format(MSG=msg) + text[line_start:]
        added += 1
    if added == 0:
        return 1
    f.write_text(text)
    print(f"patch-execprobe: probes at {added}/4 EPERM sites")
    return 0


if __name__ == "__main__":
    sys.exit(main())
