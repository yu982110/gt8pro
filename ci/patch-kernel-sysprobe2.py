#!/usr/bin/env python3
"""patch-kernel-sysprobe2.py -- bracket the syscall dispatch, so the exact hook that stops is named.

patch-kernel-sysprobe.py shows the boot's stuck process (MSUEarlyBootTask) entering `exit` and then
nothing: it never reaches exit_with_reason(), so it stops somewhere between the syscall entry and the
handler.  Between those two points xnu does three things:

    pid = proc_pid(proc);                    <- the first probe sits here
    #if CONFIG_MACF   mac_proc_check_syscall_unix(proc, syscode)   (the port's AMFI stand-in)
    AUDIT_SYSCALL_ENTER(code, proc, uthread);
    error = (*(callp->sy_call))(proc, ...);

so three more lines bracket them: B before the audit entry, C after it (just before the handler), D
right after the handler returns.  The last one printed names the step that never returned.

    python3 ci/patch-kernel-sysprobe2.py <xnu tree>

Idempotent (MARK); run after patch-kernel-sysprobe.py, which inserts the entry probe and the
lafa_watching()-equivalent filter this one reuses through the same name test.
"""
import pathlib
import sys

MARK = "HAKIOS-SYSPROBE2"
FILE = "bsd/dev/arm/systemcalls.c"
WATCH = "MSUEarlyBootTask"

HELPER = (
    "/* " + MARK + ": does this process's syscall deserve a line? */\n"
    "static inline bool\n"
    "lafa_sys_watched(struct proc *proc)\n"
    "{\n"
    "\treturn proc->p_comm[0] == '" + WATCH[0] + "' &&\n"
    "\t    strncmp(proc->p_comm, \"" + WATCH + "\", sizeof(\"" + WATCH + "\") - 1) == 0;\n"
    "}\n"
    "\n"
)

B_C = (
    "\tif (lafa_sys_watched(proc)) {\n"
    "\t\tloader_log_line(\"HAKIOS-SYS-B pre-audit\\n\");\n"
    "\t}\n"
)
C = (
    "\tif (lafa_sys_watched(proc)) {\n"
    "\t\tloader_log_line(\"HAKIOS-SYS-C pre-call\\n\");\n"
    "\t}\n"
)
D = (
    "\tif (lafa_sys_watched(proc)) {\n"
    "\t\tchar lafa_line[64];\n"
    "\n"
    "\t\tsnprintf(lafa_line, sizeof(lafa_line), \"HAKIOS-SYS-D ret %d\\n\", error);\n"
    "\t\tloader_log_line(lafa_line);\n"
    "\t}\n"
)

AUDIT = "\tAUDIT_SYSCALL_ENTER(code, proc, uthread);\n"
CALL = "\terror = (*(callp->sy_call))(proc, &uthread->uu_arg[0], &(uthread->uu_rval[0]));\n"


def main(root):
    path = pathlib.Path(root) / FILE
    if not path.exists():
        print("patch-kernel-sysprobe2: %s not found" % path)
        return 1
    text = path.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-kernel-sysprobe2: already applied")
        return 0
    if text.count(AUDIT) != 1 or text.count(CALL) != 1:
        print("patch-kernel-sysprobe2: MISS: audit %d, call %d times" % (text.count(AUDIT), text.count(CALL)))
        return 1

    text = text.replace(AUDIT, B_C + AUDIT + C, 1)
    text = text.replace(CALL, CALL + D, 1)
    # the helper goes before unix_syscall()
    head = "void\nunix_syscall(\n"
    if text.count(head) != 1:
        print("patch-kernel-sysprobe2: MISS: unix_syscall() definition")
        return 1
    text = text.replace(head, HELPER + head, 1)
    path.write_text(text, errors="surrogateescape")
    print("patch-kernel-sysprobe2: B/C/D probes bracket MACF, audit and the handler")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-sysprobe2.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
