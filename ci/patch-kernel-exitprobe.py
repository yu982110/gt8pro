#!/usr/bin/env python3
"""patch-kernel-exitprobe.py -- say how far a process's exit gets, so a spin in it is localised.

The phone reaches launchd's boot tasks and then stops: no panic, no further kernel output, and the
loader (on the other core) keeps beating, so the kernel is alive but no longer printing.  The last
thing that happens is a child of launchd exiting (`MSUEarlyBootTask: ... Goodbye!!`), which puts the
suspect between `exit_with_reason()` and the end of `proc_exit()`: the task termination, the address
space teardown, and the wait for signallers to drain that follows them.

These probes bracket that path.  Whichever line is the last one in the log after a boot names the
step that never returned.

    python3 ci/patch-kernel-exitprobe.py <xnu tree>

Idempotent (MARK); prints MISS with the anchor it could not find.
"""
import pathlib
import sys

MARK = "HAKIOS-EXITP"

# (what, anchor that must appear exactly once, text to put after the anchor)
EDITS = [
    (
        "exit_with_reason entry",
        "exit_with_reason(proc_t p, int rv, int *retval, boolean_t thread_can_terminate, boolean_t perf_notify,\n"
        "    int jetsam_flags, struct os_reason *exit_reason)\n{\n",
        '\tprintf("' + MARK + ': exit_with_reason pid %d rv %d\\n", p->p_pid, rv);\n',
    ),
    (
        "proc_exit entry",
        "proc_exit(proc_t p)\n{\n\tproc_t q;\n",
        '\tprintf("' + MARK + ': proc_exit entry pid %d\\n", p->p_pid);\n',
    ),
    (
        "around task_terminate_internal",
        "\t\tproc_prepareexit(p, 0, TRUE);\n\t\t(void) task_terminate_internal(task);\n",
        None,       # handled specially: a probe before and one after
    ),
    (
        "before the signaller drain",
        "\tif ((p->p_lflag & P_LINSIGNAL) || (p->p_sigwaitcnt > 0)) {\n",
        '\tprintf("' + MARK + ': proc_exit before sigdrain pid %d\\n", p->p_pid);\n',
    ),
    (
        "wait4_nocancel entry",
        "wait4_nocancel(proc_t q, struct wait4_nocancel_args *uap, int32_t *retval)\n{\n\tint nfound;\n",
        '\tprintf("' + MARK + ': wait4_nocancel\\n");\n',
    ),
]

BEFORE_TERMINATE = '\t\tprintf("' + MARK + ': proc_exit before task_terminate_internal pid %d\\n", p->p_pid);\n'
AFTER_TERMINATE = '\t\tprintf("' + MARK + ': proc_exit after task_terminate_internal pid %d\\n", p->p_pid);\n'


def main(root):
    f = pathlib.Path(root) / "bsd" / "kern" / "kern_exit.c"
    if not f.exists():
        print("patch-kernel-exitprobe: %s not found" % f)
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-kernel-exitprobe: already applied")
        return 0

    for what, anchor, add in EDITS:
        if text.count(anchor) != 1:
            print("patch-kernel-exitprobe: MISS(%s): anchor appears %d times" % (what, text.count(anchor)))
            return 1
        if add is None:
            new = ("\t\tproc_prepareexit(p, 0, TRUE);\n" + BEFORE_TERMINATE +
                   "\t\t(void) task_terminate_internal(task);\n" + AFTER_TERMINATE)
            text = text.replace(anchor, new, 1)
        else:
            text = text.replace(anchor, anchor + add, 1)

    f.write_text(text, errors="surrogateescape")
    print("patch-kernel-exitprobe: 6 probes on the exit path (exit_with_reason, proc_exit x3, sigdrain, wait4)")
    print("  next boot, the LAST of these lines in the log names the step that did not return")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-exitprobe.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
