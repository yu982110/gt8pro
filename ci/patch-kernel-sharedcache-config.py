#!/usr/bin/env python3
"""patch-kernel-sharedcache-config.py -- enable the shared-cache / TBI options in our machine config.

The reference (0102-pandora-machine-config-4k.patch) carries a machine config whose options include

    options CONFIG_RESLIDE_SHARED_CACHE
    options CONFIG_KERNEL_TBI

and those two are what the shared region code needs to map the dyld shared cache at its slid
address with 4 KiB pages. Our MASTER.arm64.lafa has neither, and the kernel dies silently at
dyld's "dynamic linking" step exactly when the cache is present -- the first time anything in
this port has actually opened it.

    python3 ci/patch-kernel-sharedcache-config.py <hakios tree>
"""
import glob
import pathlib
import sys

OPTIONS = [
    "options   CONFIG_RESLIDE_SHARED_CACHE",
    "options   CONFIG_KERNEL_TBI",
]

MARK = "HAKIOS-SHAREDCACHE-CONFIG"


def main(root):
    root = pathlib.Path(root)
    cands = sorted(glob.glob(str(root / "config" / "*lafa*")))
    cands += sorted(glob.glob(str(root / "**" / "*lafa*defconfig*"), recursive=True))
    if not cands:
        print("patch-sharedcache-config: MISS: no lafa machine config found")
        return 1
    f = pathlib.Path(cands[0])
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-sharedcache-config: already applied")
        return 0
    missing = [o for o in OPTIONS if o.split()[-1] not in text]
    if not missing:
        print("patch-sharedcache-config: the options are already in", f.name)
        return 0
    with f.open("a", errors="surrogateescape") as out:
        out.write("\n# %s: the shared region maps the dyld cache at its slid address\n" % MARK)
        for o in missing:
            out.write(o + "\n")
    print("patch-sharedcache-config: %s += %s" % (f.name, ", ".join(o.split()[-1] for o in missing)))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-sharedcache-config.py <hakios tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
