#!/usr/bin/env python3
"""HAKIOS-LAFA-WQ: say which workq_kernreturn operation the freeze happens in.

Every run's last logged syscall (the probe logs at entry) is

    HAKIOS-SYS launchd.darwin 368 workq_kernreturn

so the kernel stops inside workq_kernreturn().  Logging the options / affinity / prio
arguments at entry names the operation, which is what the next step needs: the switch
below has one case per operation and each calls a different subsystem.

    python3 ci/patch-kernel-workq-probe.py <xnu tree>

Idempotent; exits 0 when already applied, 1 when the anchor is gone.
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-WQ"
ANCHOR = (
    "\tint options = uap->options;\n"
    "\tint arg2 = uap->affinity;\n"
    "\tint arg3 = uap->prio;\n"
    "\tstruct workqueue *wq = proc_get_wqptr(p);\n"
    "\tint error = 0;\n"
)
BLOCK = (
    "\t/* " + MARK + ": the freeze is inside this call; the arguments name the operation. */\n"
    "\t{\n"
    "\t\tchar lafa_l[128];\n"
    "\n"
    "\t\tsnprintf(lafa_l, sizeof(lafa_l),\n"
    "\t\t    \"HAKIOS-WQ pid %d options 0x%x arg2 0x%x arg3 0x%x\\n\",\n"
    "\t\t    p->p_pid, options, arg2, arg3);\n"
    "\t\tloader_log_line(lafa_l);\n"
    "\t}\n"
)
DECL = "extern void loader_log_line(const char *line);   /* " + MARK + " */\n"


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-workq-probe.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    f = next(root.rglob("bsd/pthread/pthread_workqueue.c"), None)
    if f is None:
        print("patch-wq: MISS bsd/pthread/pthread_workqueue.c")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-wq: already applied")
        return 0
    if ANCHOR not in text:
        print("patch-wq: MISS the workq_kernreturn locals anchor")
        return 1
    text = text.replace(ANCHOR, ANCHOR + BLOCK, 1)
    if DECL not in text:
        i = text.find("#include")
        j = text.find("\n", i)
        text = text[:j + 1] + DECL + text[j + 1:]
    f.write_text(text, encoding="utf-8")
    print("patch-wq: workq_kernreturn arguments logged at entry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
