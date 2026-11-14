#!/usr/bin/env python3
"""patch-kernel-sysprobe.py -- name the syscall a process is inside when it stops.

The port's own systrace (xnu/hakios.patch, bsd/dev/arm/systemcalls.c) reports a call after it
returns, and is filtered by a TUNABLE: with `systrace=2 systrace_name=MSUEarlyBootTask` on the command
line it produced nothing at all on this build, so it cannot say where the process stopped.

This probe is written for exactly that question: at the syscall entry, before the call runs, a line
naming the process, the pid and the call goes to the loader's ring (loader_log_line, which the boot
proves works).  A process that never comes back still leaves the name of the call it entered.

    python3 ci/patch-kernel-sysprobe.py <xnu tree>

The process it watches is a #define here; MSUEarlyBootTask is the one the boot stops in.  Idempotent
(MARK); the anchor `pid = proc_pid(proc);` appears once in unix_syscall().
"""
import pathlib
import sys

MARK = "HAKIOS-SYSPROBE"
FILE = "bsd/dev/arm/systemcalls.c"
WATCH = "MSUEarlyBootTask"

OLD = "\tpid = proc_pid(proc);\n"
NEW = (
    "\tpid = proc_pid(proc);\n"
    "\n"
    "\t/* " + MARK + ": the call's name before it runs, so one that never returns is still named. */\n"
    "\tif (proc->p_comm[0] == '" + WATCH[0] + "' &&\n"
    "\t    strncmp(proc->p_comm, \"" + WATCH + "\", sizeof(\"" + WATCH + "\") - 1) == 0) {\n"
    "\t\tchar lafa_line[96];\n"
    "\n"
    "\t\tsnprintf(lafa_line, sizeof(lafa_line), \"HAKIOS-SYS %d %s\\n\", (int)code,\n"
    "\t\t    (syscode < nsysent) ? syscallnames[syscode] : \"?\");\n"
    "\t\tloader_log_line(lafa_line);\n"
    "\t}\n"
)


def main(root):
    path = pathlib.Path(root) / FILE
    if not path.exists():
        print("patch-kernel-sysprobe: %s not found" % path)
        return 1
    text = path.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-kernel-sysprobe: already applied")
        return 0
    if text.count(OLD) != 1:
        print("patch-kernel-sysprobe: MISS: `pid = proc_pid(proc);` found %d times" % text.count(OLD))
        return 1
    text = text.replace(OLD, NEW, 1)
    # the ring writer, and the syscall-name table the port's systrace already relies on
    decl_anchor = "#include <sys/syscall.h>\n"
    if "extern void loader_log_line(const char *line);" not in text:
        text = text.replace(decl_anchor, decl_anchor +
                            "extern void loader_log_line(const char *line);   /* " + MARK + " */\n", 1)
    if "extern const char *syscallnames[];" not in text:
        text = text.replace(decl_anchor, decl_anchor + "extern const char *syscallnames[];\n", 1)
    path.write_text(text, errors="surrogateescape")
    print("patch-kernel-sysprobe: every syscall of %s is named in the ring before it runs" % WATCH)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-sysprobe.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
