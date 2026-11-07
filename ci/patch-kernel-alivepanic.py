#!/usr/bin/env python3
"""bsd_init 之后 45 秒主动 panic 一次：把内核日志交给 loader。

loader 只在内核 panic / 回到它那里时把日志 ring 交给它。这个移植现在能跑过 launchd 的
ignition，于是 pid 1 不退出、没有 panic，日志就永远看不到。让内核在 bsd_init 之后 45 秒
自己 panic 一次，日志就出来了；确认之后再把这个补丁去掉。

    python3 ci/patch-kernel-alivepanic.py <xnu 源码树>
"""
import sys
from pathlib import Path

ANCHOR = '\tbsd_init_kprintf("vfs_mountroot\\n");\n'

NEW = '''\t/* HAKIOS-LAFA: the system now gets past launchd's ignition and does not panic on its own,
\t * so nothing ever hands the kernel log back to the loader. One panic, 45 seconds in, and the
\t * log comes out; the timer is taken off again once it has been read. */
\t{
\t\textern kern_return_t kernel_thread_start(thread_continuation_t, void *, thread_t *);
\t\tthread_t lafa_watchdog;
\t\tif (kernel_thread_start((thread_continuation_t)lafa_alive_panic, NULL, &lafa_watchdog) == KERN_SUCCESS) {
\t\t\tthread_deallocate(lafa_watchdog);
\t\t}
\t}
\tbsd_init_kprintf("vfs_mountroot\\n");
'''

FUNC = '''
static void
lafa_alive_panic(void *arg, wait_result_t wr)
{
\t(void)arg;
\t(void)wr;
\tassert_wait_timeout((event_t)lafa_alive_panic, THREAD_INTERRUPTIBLE, 45 * 1000, NSEC_PER_MSEC);
\tthread_block(THREAD_CONTINUE_NULL);
\tpanic("DBG|alive 45 s after bsd_init: no panic of its own, log handed over here");
}
'''


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    src = root / "bsd" / "kern" / "bsd_init.c"
    text = src.read_text(encoding="utf-8", errors="surrogateescape")
    if "lafa_alive_panic" in text:
        print("patch-alivepanic: already patched")
        return 0
    if ANCHOR not in text:
        print("patch-alivepanic: anchor not found", file=sys.stderr)
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    # the function goes right before bsd_init()
    marker = "void\nbsd_init(void)\n"
    if marker in text:
        text = text.replace(marker, FUNC + "\n" + marker, 1)
    else:
        text = FUNC + "\n" + text
    src.write_text(text, encoding="utf-8", errors="surrogateescape")
    print("patch-alivepanic: a panic 45 s after bsd_init")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
