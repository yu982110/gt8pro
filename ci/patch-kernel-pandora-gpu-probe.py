#!/usr/bin/env python3
"""patch-kernel-pandora-gpu-probe.py -- Adreno 840 bring-up, level 1 (read only).

The C source lives next to this file as pandora_gpu_probe.c.  It is the same-SoC port's
xnu/patches/0162-pandora-gpu-probe.patch (ios-popsicle, SM8850 / Adreno 840 / xnu-8796),
299 lines, kept verbatim except for the compile guard (that port's #if defined(PANDORA)
becomes #if 1 here, because this port has no such macro).  Its register addresses are already
the SM8850 "canoe" ones -- mainline gcc-kaanapali.c / gpucc-kaanapali.c plus the phone's
device tree -- so nothing about the hardware needs rewriting at this level.

What level 1 does: READS ONLY, gated on what is powered, because reading GPUCC, the GPU SMMU
or the GMU while their supply or clock is off can stall the bus and reset the phone:

    GCC (always on)
    -> GPUCC, if gcc_gpu_cfg_ahb is enabled
    -> GPU SMMU, if the CX GDSC is on and the SMMU vote clock runs (one SMR/S2CR at a time)
    -> GMU state, if the CX GDSC is on and gpu_cc_ahb and gpu_cc_cx_gmu run

cmd-db (shared memory, no clock) gives the RPMh resources the GMU votes will use later.
Before each risky read a line goes to the log and the thread waits 3.5 s, so a reset still
shows how far it got.  Enable it with boot-arg pandora_gpu=1.

Differences from the reference patch:
  * guard: that port's PANDORA macro does not exist here;
  * hook: its call site is sweet_ufs_init(), which this port does not have (the loader does
    the storage I/O), so the call goes into bsd_init() right after "bsd_init: done";
  * bsd/conf/files gets the new file registered here as well.

    python3 ci/patch-kernel-pandora-gpu-probe.py <hakios tree>
"""
import pathlib
import shutil
import sys

MARK = "HAKIOS-PANDORA-GPU-PROBE"
HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "pandora_gpu_probe.c"

CONF_LINES = [
    "bsd/dev/arm/km.c",
    "bsd/dev/arm/systemcalls.c",
]
PROTO_ANCHOR = "#include <sys/param.h>\n"
INIT_ANCHOR = '\tbsd_init_kprintf("bsd_do_post - done");\n'
INIT_ADD = INIT_ANCHOR + """\t/*
\t * HAKIOS-PANDORA-GPU-PROBE: Adreno 840 level 1 (read only) in its own thread, so the rest
\t * of bsd_init is not delayed by its 3.5 s pauses between register reads.
\t */
\tpandora_gpu_init();
"""


def main(root):
    root = pathlib.Path(root)
    gpu = root / "bsd/dev/pandora_gpu.c"
    conf = root / "bsd/conf/files"
    init = root / "bsd/kern/bsd_init.c"

    if not SRC.exists():
        print("patch-pandora-gpu-probe: %s missing" % SRC)
        return 1
    for p in (conf, init):
        if not p.exists():
            print("patch-pandora-gpu-probe: %s not found" % p)
            return 1

    # 1. the driver file, verbatim from ci/pandora_gpu_probe.c
    gpu.parent.mkdir(parents=True, exist_ok=True)
    gpu.write_text(SRC.read_text(encoding="utf-8"), encoding="utf-8")
    body = gpu.read_text(encoding="utf-8")
    n = len(body.splitlines())
    # make sure the marker is in the file, so a re-run is detectable
    if MARK not in body:
        gpu.write_text("/* %s */\n" % MARK + body, encoding="utf-8")
        n += 1
    print("patch-pandora-gpu-probe: bsd/dev/pandora_gpu.c (%d lines)" % n)

    # 2. register it with the build
    t = conf.read_text(errors="surrogateescape")
    if "bsd/dev/pandora_gpu.c" not in t:
        done = False
        for anchor in CONF_LINES:
            if anchor in t:
                t = t.replace(anchor, anchor + "\nbsd/dev/pandora_gpu.c\t\t\tstandard", 1)
                done = True
                break
        if not done:
            # last resort: append (bsd/conf/files is a plain list of paths)
            t = t.rstrip("\n") + "\nbsd/dev/pandora_gpu.c\t\t\tstandard\n"
        conf.write_text(t, errors="surrogateescape")
        print("patch-pandora-gpu-probe: bsd/conf/files += pandora_gpu.c")

    # 3. prototype + call, in bsd_init()
    t = init.read_text(errors="surrogateescape")
    if "extern void pandora_gpu_init(void)" not in t:
        if PROTO_ANCHOR not in t:
            print("patch-pandora-gpu-probe: MISS prototype anchor")
            return 1
        t = t.replace(PROTO_ANCHOR,
                      PROTO_ANCHOR + "\n/* HAKIOS-PANDORA-GPU-PROBE */\nextern void pandora_gpu_init(void);\n",
                      1)
    if "pandora_gpu_init()" not in t:
        if INIT_ANCHOR not in t:
            print("patch-pandora-gpu-probe: MISS bsd_init anchor")
            return 1
        t = t.replace(INIT_ANCHOR, INIT_ADD, 1)
        init.write_text(t, errors="surrogateescape")
        print("patch-pandora-gpu-probe: bsd_init() calls pandora_gpu_init()")
    # make the re-run detectable only now that every part is in place
    g = gpu.read_text(errors="surrogateescape")
    if MARK not in g:
        gpu.write_text("/* %s */\n" % MARK + g, encoding="utf-8")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
