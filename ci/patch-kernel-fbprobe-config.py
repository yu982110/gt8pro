#!/usr/bin/env python3
"""patch-kernel-fbprobe-config.py -- turn on the framebuffer / display probes in the machine config.

docs/boot-args.md lists `fb_beat` (MiniIOMobileFramebuffer.cpp, CONFIG_FB_BEAT) and `display_probe`
(hakios.adreno.c, CONFIG_DISPLAY_PROBE) as boot-arg gated, but the boot arg only arms a probe that was
compiled in; MASTER.arm64.lafa has neither option, so the args do nothing.  With these two the kernel
says whether anything is being drawn after backboardd starts -- the difference between "nobody paints"
and "painted but not scanned out".

    python3 ci/patch-kernel-fbprobe-config.py <hakios tree>
"""
import glob
import pathlib
import sys

OPTIONS = [
    "options   CONFIG_FB_BEAT",
    "options   CONFIG_DISPLAY_PROBE",
]

MARK = "HAKIOS-FBPROBE-CONFIG"


def main(root):
    root = pathlib.Path(root)
    cands = sorted(glob.glob(str(root / "config" / "*lafa*")))
    if not cands:
        print("patch-fbprobe-config: MISS: no lafa machine config found")
        return 1
    f = pathlib.Path(cands[0])
    text = f.read_text()
    if MARK in text:
        print("patch-fbprobe-config: already applied")
        return 0
    missing = [o for o in OPTIONS if o.split()[-1] not in text]
    if not missing:
        print("patch-fbprobe-config: the options are already in", f.name)
        return 0
    with f.open("a") as out:
        out.write("\n# %s: let boot-args fb_beat / display_probe report framebuffer traffic\n" % MARK)
        for o in missing:
            out.write(o + "\n")
    print("patch-fbprobe-config: %s += %s" % (f.name, ", ".join(o.split()[-1] for o in missing)))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: patch-kernel-fbprobe-config.py <hakios tree>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
