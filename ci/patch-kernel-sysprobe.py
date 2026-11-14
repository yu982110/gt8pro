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
    "\tif ((proc->p_comm[0] == 'M' &&\n"
    "\t     strncmp(proc->p_comm, \"MSUEarlyBootTask\", sizeof(\"MSUEarlyBootTask\") - 1) == 0) ||\n"
    "\t    (proc->p_comm[0] == 'l' &&\n"
    "\t     strncmp(proc->p_comm, \"launchd.darwin\", sizeof(\"launchd.darwin\") - 1) == 0)) {\n"
    "\t\tchar lafa_line[96];\n"
    "\n"
    "\t\tsnprintf(lafa_line, sizeof(lafa_line), \"HAKIOS-SYS %d %s\\n\", (int)code,\n"
    "\t\t    (syscode < nsysent) ? syscallnames[syscode] : \"?\");\n"
    "\t\tloader_log_line(lafa_line);\n"
    "\n"
    "\t\t/* " + MARK + ": the path an open/openat was given -- so a dyld failure such as\n"
    "\t\t * \"malformed import table\" names the file it was reading. */\n"
    "\t\tif (syscode == SYS_open || syscode == SYS_openat) {\n"
    "\t\t\tchar lafa_path[160] = \"\";\n"
    "\t\t\tchar lafa_buf2[200];\n"
    "\t\t\tsize_t lafa_done2 = 0;\n"
    "\n"
    "\t\t\t(void)copyinstr((user_addr_t)uthread->uu_arg[syscode == SYS_open ? 0 : 1],\n"
    "\t\t\t    lafa_path, sizeof(lafa_path), &lafa_done2);\n"
    "\t\t\tlafa_path[sizeof(lafa_path) - 1] = 0;\n"
    "\t\t\tsnprintf(lafa_buf2, sizeof(lafa_buf2), \"HAKIOS-OPEN %s\\n\", lafa_path);\n"
    "\t\t\tloader_log_line(lafa_buf2);\n"
    "\t\t}\n"
    "\n"
    "\t\t/* " + MARK + ": a process that aborts says why in this call's own arguments -- the reason\n"
    "\t\t * string and the payload it carries (a JSON blob, usually).  Printed here, because the\n"
    "\t\t * abort itself takes everything else down with it. */\n"
    "\t\tif (syscode == SYS_abort_with_payload) {\n"
    "\t\t\tchar lafa_why[160] = \"\";\n"
    "\t\t\tchar lafa_pay[150] = \"\";\n"
    "\t\t\tchar lafa_buf[420];\n"
    "\t\t\tsize_t lafa_done = 0;\n"
    "\n"
    "\t\t\t(void)copyinstr((user_addr_t)uthread->uu_arg[4], lafa_why, sizeof(lafa_why), &lafa_done);\n"
    "\t\t\tlafa_why[sizeof(lafa_why) - 1] = 0;\n"
    "\t\t\tif ((long long)uthread->uu_arg[3] > 0) {\n"
    "\t\t\t\tsize_t want = (size_t)uthread->uu_arg[3];\n"
    "\t\t\t\tif (want > sizeof(lafa_pay) - 1) {\n"
    "\t\t\t\t\twant = sizeof(lafa_pay) - 1;\n"
    "\t\t\t\t}\n"
    "\t\t\t\tif (copyin((user_addr_t)uthread->uu_arg[2], lafa_pay, want) == 0) {\n"
    "\t\t\t\t\tfor (size_t k = 0; k < want; k++) {\n"
    "\t\t\t\t\t\tif ((unsigned char)lafa_pay[k] < 0x20 || (unsigned char)lafa_pay[k] > 0x7e) {\n"
    "\t\t\t\t\t\t\tlafa_pay[k] = '.';\n"
    "\t\t\t\t\t\t}\n"
    "\t\t\t\t\t}\n"
    "\t\t\t\t\tlafa_pay[want] = 0;\n"
    "\t\t\t\t}\n"
    "\t\t\t}\n"
    "\t\t\tsnprintf(lafa_buf, sizeof(lafa_buf),\n"
    "\t\t\t    \"HAKIOS-ABORT ns=%lld code=%lld size=%lld why=\\\"%s\\\" payload=\\\"%s\\\"\\n\",\n"
    "\t\t\t    (long long)uthread->uu_arg[0], (long long)uthread->uu_arg[1],\n"
    "\t\t\t    (long long)uthread->uu_arg[3], lafa_why, lafa_pay);\n"
    "\t\t\tloader_log_line(lafa_buf);\n"
    "\t\t}\n"
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
