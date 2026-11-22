#!/usr/bin/env python3
"""HAKIOS-LAFA-PROCINFO-GUARD: a PIDINFO about a process that has just exited stops the machine.

2026-11-22 10:09 (gpu60): the last line on the screen before the phone went to EDL was

    HAKIOS-PROCI entry pid 53 call 2 flavor 13

with no "done" line, right after

    HAKIOS-EXITP: proc_exit entry pid 53
    HAKIOS-EXITP: before task_terminate_internal pid 53
    HAKIOS-EXITP: after task_terminate_internal pid 53

and PIDINFO flavor 15, 8 and 18 on the same pid had just succeeded (each with its done line).
flavor 13 is PROC_PIDT_SHORTBSDINFO; in the persisted ring every other flavor-13 call -- always
about a live process -- completes.  So: refuse PIDINFO for a zombie before the flavour handler
runs.  lchd gets ESRCH, which is the right answer for a reaped child anyway.

Idempotent; exits 0 when already applied, 1 when the probe's call site is not there (run
patch-kernel-procinfo-probe.py first).

    python3 ci/patch-kernel-procinfo-guard.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-PROCI-GUARD"

ANCHOR = (
    "\tint lafa_ret = proc_info_internal(uap->callnum, uap->pid, 0, 0, uap->flavor, uap->arg,\n"
    "\t    uap->buffer, uap->buffersize, retval);\n"
)

INSERT = (
    "\t/* " + MARK + ": a PIDINFO about a zombie stops the machine (see the header). */\n"
    "\tif (uap->callnum == PROC_INFO_CALL_PIDINFO && uap->pid != 0) {\n"
    "\t\tproc_t lafa_target = proc_find(uap->pid);\n"
    "\n"
    "\t\tif (lafa_target != PROC_NULL) {\n"
    "\t\t\tint lafa_zombie = (lafa_target->p_stat == SZOMB);\n"
    "\n"
    "\t\t\tproc_rele(lafa_target);\n"
    "\t\t\tif (lafa_zombie) {\n"
    "\t\t\t\tchar lafa_l[128];\n"
    "\n"
    "\t\t\t\tsnprintf(lafa_l, sizeof(lafa_l),\n"
    "\t\t\t\t    \"HAKIOS-PROCI-GUARD: pid %d flavor %d is a zombie -> ESRCH\\n\",\n"
    "\t\t\t\t    uap->pid, (int)uap->flavor);\n"
    "\t\t\t\tloader_log_line(lafa_l);\n"
    "\t\t\t\treturn ESRCH;\n"
    "\t\t\t}\n"
    "\t\t}\n"
    "\t}\n"
    "\n"
)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: patch-kernel-procinfo-guard.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    f = next(root.rglob("bsd/kern/proc_info.c"), None)
    if f is None:
        print("patch-proci-guard: MISS bsd/kern/proc_info.c")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-proci-guard: already applied")
        return 0
    if ANCHOR not in text:
        print("patch-proci-guard: MISS the probe's call site")
        return 1
    text = text.replace(ANCHOR, INSERT + ANCHOR, 1)
    f.write_text(text, encoding="utf-8")
    print("patch-proci-guard: zombie guard inserted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
