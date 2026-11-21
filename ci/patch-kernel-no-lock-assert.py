#!/usr/bin/env python3
"""Make the lock assertions no-ops: check nothing, panic on nothing.

Why: every run of this kernel stops before it has printed a byte -- heartbeat 0, the
kernel's ring empty, silence.  The one thing that looks exactly like that is a live lock
assertion: `lck_rw_assert` / `lck_mtx_assert` panic from the middle of early boot, before
kprintf has anywhere to go.  The port that boots iOS on this same SoC patches eleven such
assertions out of its prelinked kernel, which is the same statement in binary form.

Turning MACH_ASSERT off is the tempting one-line answer and does not work here: it leaves
`_debug4k_proc_filter`, `_debug4k_proc_name` and `_vm_footprint_suspend_allowed`
undefined at link time (they are only defined with assertions on).  So this patch keeps
MACH_ASSERT on and only empties the two functions the assertions funnel through: the
checks inside them never run, everything else (and every symbol) stays where it was.

usage: patch-kernel-no-lock-assert.py <xnu source dir>
"""
import pathlib
import sys

MARK = "HAKIOS-NO-LOCK-ASSERT"


def one(root, rel, old, new, what):
    f = pathlib.Path(root) / rel
    if not f.exists():
        print("patch-no-lock-assert: %s not found" % rel)
        return 1
    t = f.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-no-lock-assert: %s already done" % what)
        return 0
    if old not in t:
        print("patch-no-lock-assert: MISS in %s (%s)" % (rel, what))
        return 1
    f.write_text(t.replace(old, new, 1), errors="surrogateescape")
    print("patch-no-lock-assert: %s is a no-op now" % what)
    return 0


RW_OLD = """\tthread_t thread = current_thread();

\tswitch (type) {
"""

RW_NEW = """\tthread_t thread __unused = current_thread();

\t/* HAKIOS-NO-LOCK-ASSERT: see ci/patch-kernel-no-lock-assert.py -- a live lock
\t * assertion stops the boot before it can say anything; this one never fires now. */
\treturn;

\tswitch (type) {
"""

MTX_OLD = """\tlck_mtx_state_t state  = os_atomic_load(&lock->lck_mtx, relaxed);
\tthread_t        thread = current_thread();

\tif (type == LCK_MTX_ASSERT_OWNED) {
"""

MTX_NEW = """\tlck_mtx_state_t state  = os_atomic_load(&lock->lck_mtx, relaxed);
\tthread_t        thread = current_thread();

\t/* HAKIOS-NO-LOCK-ASSERT: see ci/patch-kernel-no-lock-assert.py. */
\t(void)state;
\t(void)thread;
\treturn;

\tif (type == LCK_MTX_ASSERT_OWNED) {
"""


def main(root):
    rc = 0
    rc |= one(root, "osfmk/kern/lock_rw.c", RW_OLD, RW_NEW, "lck_rw_assert")
    rc |= one(root, "osfmk/kern/lock_mtx.c", MTX_OLD, MTX_NEW, "lck_mtx_assert")
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
