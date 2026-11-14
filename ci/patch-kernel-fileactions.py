#!/usr/bin/env python3
"""HAKIOS-LAFA-FILEACT: name (and unblock) the failing spawn file action.

The numbered audit proved posix_spawn fails at exec_handle_file_actions(): the file
actions launchd/xpcproxy pass down fail with EPERM and the whole spawn dies.  The one
action that can return EPERM there is PSFA_INHERIT -> fp_guard_exception() (inheriting a
guarded descriptor is refused), which this kernel does not model.

Two edits:
  1. log action type / fd / errno whenever a file action fails (HAKIOS-FACT:);
  2. make PSFA_INHERIT ignore the guard error and clear CLOEXEC anyway.

Idempotent; exits 0 when already applied, 1 when an anchor is gone.
"""
import pathlib
import sys

MARK = "/* HAKIOS-LAFA-FILEACT */"
GUARD_MARK = "/* HAKIOS-LAFA-GUARD */"

ANCHOR_ERR = (
    "\t\tif (error) {\n"
    "\t\t\tif (PSFA_OPEN == psfa->psfaa_type) {\n"
)
PROBE = (
    MARK + "\n"
    "\t\t\t{\n"
    "\t\t\t\tchar lafa_fa[112];\n"
    "\t\t\t\tsnprintf(lafa_fa, sizeof(lafa_fa),\n"
    "\t\t\t\t    \"HAKIOS-FACT: type %d fd %d err %d\\n\",\n"
    "\t\t\t\t    (int)psfa->psfaa_type, psfa->psfaa_filedes, error);\n"
    "\t\t\t\tloader_log_line(lafa_fa);\n"
    "\t\t\t}\n"
)

ANCHOR_GUARD = (
    "\t\t\t} else if (fp->fp_guard_attrs) {\n"
    "\t\t\t\terror = fp_guard_exception(p, psfa->psfaa_filedes,\n"
    "\t\t\t\t    fp, kGUARD_EXC_NOCLOEXEC);\n"
)
GUARD = (
    "\t\t\t\t" + GUARD_MARK + ": the guard check is not modelled; inherit anyway\n"
    "\t\t\t\terror = 0;\n"
    "\t\t\t\tfp->fp_flags &= ~FP_CLOEXEC;\n"
)


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-fileactions.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    hits = 0
    for p in root.rglob("kern_exec.c"):
        text = p.read_text(errors="replace")
        if MARK in text and GUARD_MARK in text:
            print("patch-fileactions: already applied in", p)
            hits += 1
            continue
        changed = False
        if MARK not in text and ANCHOR_ERR in text:
            text = text.replace(ANCHOR_ERR, ANCHOR_ERR + PROBE, 1)
            changed = True
        if GUARD_MARK not in text and ANCHOR_GUARD in text:
            text = text.replace(ANCHOR_GUARD, ANCHOR_GUARD + GUARD, 1)
            changed = True
        if changed:
            p.write_text(text)
            print("patch-fileactions: probe + guard bypass in", p)
            hits += 1
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
