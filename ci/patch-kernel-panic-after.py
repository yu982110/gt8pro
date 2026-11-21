#!/usr/bin/env python3
"""Panic on purpose after N seconds, so the loader gets the kernel's log.

The loader can read the kernel's log while the kernel runs, but on this build it always
reads zero (the live struct's address does not come out right), and the kernel only hands
its log over when it *returns*.  A run that keeps running therefore tells us nothing --
which is exactly the state a working bring-up reaches.

So: a boot argument, `hakios_panic_after=N`.  Every N seconds the kernel already runs a
timer (hv_wdt_pet, which feeds the watchdog and bumps loader_live.heartbeat); this patch
makes that timer panic once N seconds have gone by since it started.  The panic goes down
the normal path: the loader sees the kernel return, writes the log (loader log + the
kernel's own ring + the panic buffer) to the last megabyte of hybridswap, and Android can
read it -- or `fastboot oem log` + `get_staged` takes it straight away.

usage: patch-kernel-panic-after.py <xnu source dir>
"""
import pathlib
import sys

MARK = "HAKIOS-PANIC-AFTER"

OLD = """\tif (++loader_live.heartbeat % MEMORY_REPORT_BEATS == 0) {
\t\tmemory_report();
\t}
"""

NEW = """\t/*
\t * HAKIOS-PANIC-AFTER: "hakios_panic_after=N" -- panic once N seconds have gone by.
\t * The log of a run that never returns is otherwise unreachable, and a bring-up that
\t * works is exactly a run that never returns.
\t */
\t{
\t\tstatic int32_t after = -1;
\t\tstatic uint64_t started;
\t\tuint64_t now;

\t\tif (after < 0) {
\t\t\tafter = 0;
\t\t\tPE_parse_boot_argn("hakios_panic_after", &after, sizeof(after));
\t\t\tclock_get_uptime(&started);
\t\t}
\t\tif (after > 0 && clock_get_uptime(&now) == 0) {
\t\t\tuint64_t ns = now - started;

\t\t\tif (ns >= (uint64_t)after * NSEC_PER_SEC) {
\t\t\t\tpanic("HAKIOS-PANIC-AFTER %d s: the kernel's log is handed to the loader now", after);
\t\t\t}
\t\t}
\t}
\tif (++loader_live.heartbeat % MEMORY_REPORT_BEATS == 0) {
\t\tmemory_report();
\t}
"""


def main(root):
    f = pathlib.Path(root) / "osfmk/arm64/loader_return.c"
    if not f.exists():
        print("patch-panic-after: osfmk/arm64/loader_return.c not found")
        return 1
    t = f.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-panic-after: already there")
        return 0
    if OLD not in t:
        print("patch-panic-after: MISS: hv_wdt_pet's heartbeat block is not the expected shape")
        return 1
    f.write_text(t.replace(OLD, NEW, 1), errors="surrogateescape")
    print("patch-panic-after: hakios_panic_after=N is in")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
