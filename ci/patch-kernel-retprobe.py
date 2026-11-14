#!/usr/bin/env python3
"""HAKIOS-LAFA-RET: print the errno of every failed syscall of a watched process.

logd and distnoted are spawned by xpcproxy and die with
`posix_spawn(...), error 0xd` (EPERM).  Every explicit EPERM exit inside
posix_spawn/execve/exec_mach_imgact has been probed and none of them fires, so the
question is now whether the kernel even returns EPERM or whether libxpc invents it.

This patch sits right after the syscall handler in unix_syscall() and logs
`code` and `error` for xpcproxy, so one boot shows which syscall fails and with what.

Idempotent; exits 0 when already applied, 1 when the anchor is gone.
"""
import pathlib
import sys

MARK = "/* HAKIOS-LAFA-RET */"
ANCHOR = (
    "\terror = (*(callp->sy_call))(proc, &uthread->uu_arg[0], &(uthread->uu_rval[0]));\n"
    "\tAUDIT_SYSCALL_EXIT(code, proc, uthread, error);\n"
)
PROBE = (
    MARK + "\n"
    "\tif (error != 0 && proc->p_comm[0] == 'x' &&\n"
    "\t    strncmp(proc->p_comm, \"xpcproxy\", sizeof(\"xpcproxy\") - 1) == 0) {\n"
    "\t\tchar lafa_ret[96];\n"
    "\t\tsnprintf(lafa_ret, sizeof(lafa_ret), \"HAKIOS-RET: code %d err %d\\n\", code, error);\n"
    "\t\tloader_log_line(lafa_ret);\n"
    "\t}\n"
)


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-retprobe.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    hits = 0
    for rel in ("bsd/dev/arm/systemcalls.c", "bsd/dev/arm64/systemcalls.c"):
        f = root / rel
        if not f.exists():
            continue
        text = f.read_text(errors="replace")
        if MARK in text:
            print("patch-retprobe: already applied in", rel)
            hits += 1
            continue
        if ANCHOR not in text:
            print("patch-retprobe: MISS anchor in", rel)
            continue
        text = text.replace(ANCHOR, ANCHOR + PROBE, 1)
        f.write_text(text)
        print("patch-retprobe: syscall errno probe added to", rel)
        hits += 1
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
