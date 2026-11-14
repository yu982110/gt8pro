#!/usr/bin/env python3
"""patch-kernel-exitprobe.py -- say how far a process's exit gets, on the channel that works.

The first version of this patch used printf().  Nothing came out, and the run that finally showed
why was the syscall probe: it writes through loader_log_line() -- the port's own ring writer, the
same one the HAKIOS-TICK heartbeat uses -- and 691 of its lines arrived, while every printf-based line
of the exit probes stayed silent.  (This kernel is a DEVELOPMENT build: pe_kprintf.c leaves
disable_kprintf_output true unless -enable_kprintf_spam is given, so printf is not a channel one can
rely on here.)

The process the boot stops in enters `exit`, clears MACF and the audit hook, and never comes back out
of the handler.  These six probes bracket that handler, now written with snprintf + loader_log_line so
they are actually visible.  Whichever line is last names the step that never returned.

    python3 ci/patch-kernel-exitprobe.py <xnu tree>

Idempotent (MARK); the anchors are the same ones the printf version used.
"""
import pathlib
import sys

MARK = "HAKIOS-EXITP"
RING_DECL = "extern void loader_log_line(const char *line);   /* " + MARK + " */\n"


def probe(indent, fmt, args=""):
    """A line to the ring: formatted into a local buffer, then written in one go."""
    call = '\tsnprintf(lafa_l, sizeof(lafa_l), "%s\\n"%s);' % (fmt, (", " + args) if args else "")
    return "{%schar lafa_l[120];%s loader_log_line(lafa_l); }\n" % (
        indent, indent + call.replace("\t", " "))


P_EXIT = probe("\t\t", MARK + ": exit_with_reason pid %d rv %d", "p->p_pid, rv")
P_PROCENTRY = probe("\t", MARK + ": proc_exit entry pid %d", "p->p_pid")
P_BEFORE_TERM = probe("\t\t", MARK + ": before task_terminate_internal pid %d", "p->p_pid")
P_AFTER_TERM = probe("\t\t", MARK + ": after task_terminate_internal pid %d", "p->p_pid")
P_SIGDRAIN = probe("\t", MARK + ": before sigdrain pid %d", "p->p_pid")
P_WAIT4 = probe("\t", MARK + ": wait4_nocancel")

EDITS = [
    ("exit_with_reason entry",
     "exit_with_reason(proc_t p, int rv, int *retval, boolean_t thread_can_terminate, boolean_t perf_notify,\n"
     "    int jetsam_flags, struct os_reason *exit_reason)\n{\n",
     P_EXIT),
    ("proc_exit entry",
     "proc_exit(proc_t p)\n{\n\tproc_t q;\n",
     P_PROCENTRY),
    ("before the signaller drain",
     "\tif ((p->p_lflag & P_LINSIGNAL) || (p->p_sigwaitcnt > 0)) {\n",
     P_SIGDRAIN),
    ("wait4_nocancel entry",
     "wait4_nocancel(proc_t q, struct wait4_nocancel_args *uap, int32_t *retval)\n{\n\tint nfound;\n",
     P_WAIT4),
]

TERM_ANCHOR = "\t\tproc_prepareexit(p, 0, TRUE);\n\t\t(void) task_terminate_internal(task);\n"


def main(root):
    path = pathlib.Path(root) / "bsd" / "kern" / "kern_exit.c"
    if not path.exists():
        print("patch-kernel-exitprobe: %s not found" % path)
        return 1
    text = path.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-kernel-exitprobe: already applied")
        return 0

    for what, anchor, add in EDITS:
        if text.count(anchor) != 1:
            print("patch-kernel-exitprobe: MISS(%s): anchor appears %d times" % (what, text.count(anchor)))
            return 1
        text = text.replace(anchor, anchor + add, 1)

    if text.count(TERM_ANCHOR) != 1:
        print("patch-kernel-exitprobe: MISS(task_terminate_internal): %d times" % text.count(TERM_ANCHOR))
        return 1
    head, tail = TERM_ANCHOR.split("\n", 1)
    text = text.replace(TERM_ANCHOR, head + "\n" + P_BEFORE_TERM + tail + P_AFTER_TERM, 1)

    # the declaration has to come before the first use
    anchor = "int\nexit_with_reason(proc_t p, int rv, int *retval, boolean_t thread_can_terminate, boolean_t perf_notify,\n"
    if text.count(anchor) != 1:
        print("patch-kernel-exitprobe: MISS(declaration spot)")
        return 1
    text = text.replace(anchor, RING_DECL + anchor, 1)

    path.write_text(text, errors="surrogateescape")
    print("patch-kernel-exitprobe: 6 probes, all through loader_log_line (the channel that prints)")
    print("  the last one in the log names the step of the exit that never returned")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-exitprobe.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
