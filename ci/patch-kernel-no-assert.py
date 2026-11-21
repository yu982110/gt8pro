#!/usr/bin/env python3
"""Drop mach_assert from the DEVELOPMENT target: assertions off, debugging kept.

Why: every run of this kernel stops with heartbeat 0 and not one byte in loader_log --
the shape of a kernel that dies before kprintf is up.  With `mach_assert` on, every
lck_rw_assert / lck_mtx_assert / ASSERT in the early boot path is live, and one of them
failing is exactly that: silence before any output.  The port that boots iOS on the same
SoC ships a release-style kernel whose assertion patches are six of its eleven binary
fixes (PRNG, FIPS POST x4, CoreCrypto, lock_rw x11) -- all of that is `mach_assert`
being on.

RELEASE is not the answer here: its BSD_RELEASE carries no_kprintf_str, which would take
kprintf out of the kernel we are trying to watch.  Removing the single option keeps every
other development facility and only turns the assertions off.

usage: patch-kernel-no-assert.py <xnu source dir>
"""
import pathlib
import sys

MARK = "HAKIOS-NO-ASSERT"

OLD = "#  KERNEL_DEV =     [ KERNEL_BASE development mach_assert config_xnupost proc_ref_debug ]"
NEW = (
    "# HAKIOS-NO-ASSERT: mach_assert dropped from KERNEL_DEV (see ci/patch-kernel-no-assert.py)\n"
    "#  KERNEL_DEV =     [ KERNEL_BASE development config_xnupost proc_ref_debug ]"
)


def main(root):
    candidates = [pathlib.Path(root) / "config/MASTER.arm64.bcm2837",
                  pathlib.Path(root) / "config/MASTER"]
    f = next((c for c in candidates if c.exists() and OLD in c.read_text(errors="surrogateescape")), None)
    if f is None:
        f = candidates[0]
    if not f.exists():
        print("patch-no-assert: config/MASTER not found")
        return 1
    t = f.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-no-assert: already applied")
        return 0
    if OLD not in t:
        print("patch-no-assert: MISS: KERNEL_DEV is not the expected shape")
        for line in t.splitlines():
            if "KERNEL_DEV" in line:
                print("   found: " + line)
        return 1
    f.write_text(t.replace(OLD, NEW, 1), errors="surrogateescape")
    print("patch-no-assert: KERNEL_DEV no longer sets mach_assert")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
