#!/usr/bin/env python3
"""patch-kernel-initwait.py -- keep the screen (and the loader's log) alive when initproc dies.

When launchd cannot start, the kernel panics and the machine resets within a second or two: the
console text that says why (dyld's messages, the shared region, ignition) is gone before a photo or
"oem logtail" can catch it. Every round of debugging therefore ends in a black screen.

The same-SoC reference does this in 0123-pandora-initproc-panic-wait.patch: before the coredump and
the panic, if the dying proc is initproc and a boot-arg says how long, block the thread for that
many seconds. During the wait the kernel is still alive, so "oem logtail" over fastboot can pull
the log, and the console text stays on screen.

Boot with e.g. hakios_initwait=300 (seconds, capped at 600).

    python3 ci/patch-kernel-initwait.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-INITWAIT"

ANCHOR = """	proc_crash_coredump(p);

	sync(p, (void *)NULL, (int *)NULL);
"""

NEW = """#if defined(HAKIOS_INITWAIT) || 1
	/*
	 * HAKIOS-INITWAIT: when process 1 dies the panic follows within about a second and the
	 * machine resets, so the console text that says why is lost before it can be read. With
	 * hakios_initwait=N, wait N seconds (up to 600) here first: the kernel stays alive, so
	 * "fastboot oem logtail" can pull the log and the screen keeps the last words.
	 */
	uint32_t hakios_wait = 0;
	if (p == initproc &&
	    PE_parse_boot_argn("hakios_initwait", &hakios_wait, sizeof(hakios_wait)) &&
	    hakios_wait != 0) {
		if (hakios_wait > 600) {
			hakios_wait = 600;
		}
		printf("HAKIOS: initproc exiting (%s); holding the panic for %u s\\n",
		    prefix_str, hakios_wait);
		assert_wait_timeout((event_t)&hakios_wait, THREAD_UNINT, hakios_wait, NSEC_PER_SEC);
		thread_block(THREAD_CONTINUE_NULL);
	}
#endif

	proc_crash_coredump(p);

	sync(p, (void *)NULL, (int *)NULL);
"""


def main(root):
    f = pathlib.Path(root) / "bsd/kern/kern_exit.c"
    if not f.exists():
        print(f"patch-initwait: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-initwait: already applied")
        return 0
    if ANCHOR not in text:
        print("patch-initwait: MISS: proc_crash_coredump context not found")
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-initwait: initproc exit now waits for hakios_initwait seconds")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-initwait.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
