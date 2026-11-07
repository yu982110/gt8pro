#!/usr/bin/env python3
"""patch-kernel-skip-debug-init.py -- skip the debug-only inits that hang on lafa.

Probes in osfmk/kern/startup.c's kernel_bootstrap() reach

    phys_carveout_init returned     <- last line
    (nothing)
    kernel_startup_initialize_upto(STARTUP_SUB_SYSCTL)

and stop there. Between those two lines are four calls, every one of them inside its own
#ifdef -- debugging facilities, none of which this port uses or can use:

    #if MACH_KDP            kdp_init();                 <- the KDP remote-debug stub
    #if ALTERNATE_DEBUGGER  alternate_debugger_init();
    #if HYPERVISOR          hv_support_init();
    #if CONFIG_TELEMETRY    bootprofile_init();

A DEBUG kernel has MACH_KDP on, and kdp_init() is the one that touches the debug transport; it
hangs with interrupts off, which is why nothing is printed and the machine just stops.

This patch makes all four no-ops (and leaves a log line through loader_log_line so the boot
sequence still shows they were skipped).

    python3 ci/patch-kernel-skip-debug-init.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-SKIPDBG"

OLD = """#if MACH_KDP
	kernel_bootstrap_log("kdp_init");
	kdp_init();
#endif

#if ALTERNATE_DEBUGGER
	alternate_debugger_init();
#endif

#if HYPERVISOR
	kernel_bootstrap_thread_log("hv_support_init");
	hv_support_init();
#endif

#if CONFIG_TELEMETRY
	kernel_bootstrap_log("bootprofile_init");
	bootprofile_init();
#endif
"""

NEW = """/*
 * HAKIOS-LAFA-SKIPDBG: none of these four run on this port, and one of them (kdp_init, which a
 * DEBUG kernel builds in) never returns here: it takes the boot down with interrupts off, so
 * nothing is printed and the machine simply stops. They are all debugging facilities, so they are
 * skipped rather than debugged. The loader still sees a step, through its own log.
 */
extern void loader_log_line(const char *line);
loader_log_line("probe: (skipped kdp/altdbg/hv/telemetry init)\\n");
"""


def main(root):
    f = pathlib.Path(root) / "osfmk/kern/startup.c"
    if not f.exists():
        print(f"patch-skipdbg: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-skipdbg: already applied")
        return 0
    if OLD not in text:
        print("patch-skipdbg: MISS: the four debug inits are not in the expected shape")
        return 1
    text = text.replace(OLD, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-skipdbg: kdp_init / alternate_debugger_init / hv_support_init / bootprofile_init skipped")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-skip-debug-init.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
