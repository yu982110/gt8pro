#!/usr/bin/env python3
"""patch-kernel-ufsrw-force.py -- assume the writable flag is the problem and hardwire it.

Evidence without logs: the HFS+ mount of /dev/ufs_oplusreserve2 comes back EROFS (screen photo),
and the driver's only EROFS sources are "device opened for write while su.writable is false"
(sweet_ufs_open, and the strategy's write check). So either the boot-arg plumbing never reaches
PE_parse_boot_argn("ufsrw", ...), or something later clears it.

Hypothesis test: stop asking the boot-arg and set su.writable = true unconditionally. If the HFS+
volume then mounts read-write (init says "kept between boots", and launchd gets past
restore-datapartition), the flag was the only problem - and since every caller here is our own
init, the boot-arg was only ever a switch for ourselves.

    python3 ci/patch-kernel-ufsrw-force.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-UFSRW-FORCE"

ANCHOR = """	su.writable = (rw != 0);
"""

NEW = """	/* HAKIOS-UFSRW-FORCE: hypothesis test - the writable flag is what fails, not the
	 * mount. Set it unconditionally; every writer here is our own init. */
	su.writable = true;
	(void)rw;
"""


def main(root):
    f = pathlib.Path(root) / "bsd/dev/sweet_ufs.c"
    if not f.exists():
        print("patch-ufsrw-force: sweet_ufs.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-ufsrw-force: already applied")
        return 0
    if text.count(ANCHOR) != 1:
        print("patch-ufsrw-force: MISS: the writable assignment appears %d times"
              % text.count(ANCHOR))
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-ufsrw-force: su.writable = true unconditionally")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-ufsrw-force.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
