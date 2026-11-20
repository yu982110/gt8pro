#!/usr/bin/env python3
"""HAKIOS-LAFA-WQPH: name the phase of workq_thread_return() that never finishes.

The workq probe showed the last syscall is workq_kernreturn with options 0x100
(WQOPS_THREAD_WORKLOOP_RETURN), which is

    case WQOPS_THREAD_WORKLOOP_RETURN:
    case WQOPS_THREAD_RETURN:
        error = workq_thread_return(p, uap, wq);

workq_thread_return() has five phases; the interesting one is workq_lock_spin(wq),
which spins with interrupts masked -- a lock held by a dead or self thread there would
freeze the kernel with no panic and no reboot, exactly what we see.  One ring line at
each phase boundary names the phase the boot stops in.

    python3 ci/patch-kernel-workq-phases.py <xnu tree>

Idempotent; exits 0 when already applied, 1 when any anchor is gone.
"""
import pathlib
import sys

MARK = "HAKIOS-WQPH"
DECL = "extern void loader_log_line(const char *line);   /* " + MARK + " */\n"


def probe(n):
    return ('\tloader_log_line("HAKIOS-WQPH: phase %d\\n");   /* %s */\n' % (n, MARK))


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-workq-phases.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    f = next(root.rglob("bsd/pthread/pthread_workqueue.c"), None)
    if f is None:
        print("patch-wqph: MISS bsd/pthread/pthread_workqueue.c")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-wqph: already applied")
        return 0

    # (anchor, insert_before?)
    edits = [
        # 1: entered workq_thread_return, past the EINVAL checks
        ("\t/* reset signal mask on the workqueue thread to default state */\n", True),
        # 2: before the kevent delivery through the pthread functions
        ("\tif (kqr) {\n"
         "\t\tuint32_t upcall_flags = WQ_FLAG_THREAD_NEWSPI | WQ_FLAG_THREAD_REUSE;\n", True),
        # 3: after the kevent delivery (long context: that assert appears earlier too)
        ("\t\tassert(uth->uu_kqr_bound == NULL);\n"
         "\t}\n"
         "\n"
         "\tWQ_TRACE_WQ(TRACE_wq_runthread | DBG_FUNC_END, wq, uap->options, 0, 0);\n", True),
        # 4: just before taking the workqueue spinlock (that call appears earlier too)
        ("#if CONFIG_WORKLOOP_DEBUG\n"
         "\tUU_KEVENT_HISTORY_WRITE_ENTRY(uth, { .uu_error = -1, });\n"
         "#endif\n"
         "\n"
         "\tworkq_lock_spin(wq);\n", True),
        # 5: lock held, about to park or pick the next threadreq
        ("\tworkq_select_threadreq_or_park_and_unlock(p, wq, uth,\n"
         "\t    WQ_SETUP_CLEAR_VOUCHER);\n", True),
    ]

    n = 0
    for anchor, before in edits:
        n += 1
        if anchor not in text:
            print("patch-wqph: MISS anchor %d" % n)
            return 1
        if before:
            text = text.replace(anchor, probe(n) + anchor, 1)
        else:
            text = text.replace(anchor, anchor + probe(n), 1)
        print("patch-wqph: phase %d armed" % n)

    if DECL not in text:
        i = text.find("#include")
        j = text.find("\n", i)
        text = text[:j + 1] + DECL + text[j + 1:]
    f.write_text(text, encoding="utf-8")
    print("patch-wqph: 5 phase probes in workq_thread_return")
    return 0


if __name__ == "__main__":
    sys.exit(main())
