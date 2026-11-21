#!/usr/bin/env python3
"""patch-kernel-pandora-gpu-probe.py -- Adreno 840 bring-up, the reference port's full driver.

This installs the same-SoC reference port's pandora_gpu.c (levels 1-15, 4048 lines, from
refs/data-a-live/wsl/pandora_gpu.c) instead of the hand-written level-1 probe, together with:

  bsd/dev/pandora_glue.c      the five symbols the driver wants from that port's tree, done
                              for this one (firmware pack, SCM, and three stubs)
  bsd/dev/adreno/*.c/h        ArmSmmu4K (MIT) and the NativeAdreno ABI header, for the GMU's
                              own SMMU context at level 4a

The driver is verbatim except for two mechanical changes (tools/port-full-driver.sh):
  printf() -> kprintf()        (this kernel's log ring only records kprintf/loader_log_line)
  #if defined(PANDORA) -> #if 1 (this port has no such macro)

Everything lands in bsd/conf/files so the build picks the new objects up, and bsd_init() calls
pandora_gpu_init() right after "bsd_do_post - done" (the one call site verified on the phone).

    python3 ci/patch-kernel-pandora-gpu-probe.py <hakios tree>
"""
import pathlib
import shutil
import sys

MARK = "HAKIOS-PANDORA-GPU-PROBE"
HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "pandora_gpu_full.c"
GLUE = HERE / "pandora_glue.c"
ADRENO = HERE / "adreno"
ADRENO_C = ["ArmSmmu4K.c", "ArmSmmu4KContext.c", "ArmSmmu4KMmio.c", "ArmSmmu4KProgram.c",
            "ArmSmmu4KStreams.c"]
CONF_LINES = [
    "bsd/dev/arm/km.c",
    "bsd/dev/arm/systemcalls.c",
]
CONF_NEW = ["bsd/dev/pandora_gpu.c", "bsd/dev/pandora_glue.c"] + \
           ["bsd/dev/adreno/" + c for c in ADRENO_C]
PROTO_ANCHOR = "#include <sys/param.h>\n"
INIT_ANCHOR = '\tbsd_init_kprintf("bsd_do_post - done");\n'
INIT_ADD = INIT_ANCHOR + """\t/*
\t * HAKIOS-PANDORA-GPU-PROBE: the Adreno 840 bring-up in its own thread, so the rest of
\t * bsd_init is not delayed by its pauses between register reads.
\t */
\tpandora_gpu_init();
"""


def main(root):
    root = pathlib.Path(root)
    gpu = root / "bsd/dev/pandora_gpu.c"
    glue = root / "bsd/dev/pandora_glue.c"
    adreno = root / "bsd/dev/adreno"
    conf = root / "bsd/conf/files"
    init = root / "bsd/kern/bsd_init.c"

    if not SRC.exists():
        print("patch-pandora-gpu-probe: %s missing (run tools/port-full-driver.sh)" % SRC)
        return 1
    if not GLUE.exists():
        print("patch-pandora-gpu-probe: %s missing" % GLUE)
        return 1
    for p in (conf, init):
        if not p.exists():
            print("patch-pandora-gpu-probe: %s not found" % p)
            return 1

    # 1. the driver, the glue and the ArmSmmu4K library
    gpu.parent.mkdir(parents=True, exist_ok=True)
    gpu.write_text(SRC.read_text(encoding="utf-8"), encoding="utf-8")
    glue.write_text(GLUE.read_text(encoding="utf-8"), encoding="utf-8")
    adreno.mkdir(parents=True, exist_ok=True)
    copied = 0
    for f in sorted(ADRENO.iterdir()):
        if f.is_file():
            shutil.copyfile(f, adreno / f.name)
            copied += 1
    print("patch-pandora-gpu-probe: bsd/dev/pandora_gpu.c (%d lines, the reference driver)"
          % len(gpu.read_text(encoding="utf-8").splitlines()))
    print("patch-pandora-gpu-probe: bsd/dev/pandora_glue.c")
    print("patch-pandora-gpu-probe: bsd/dev/adreno/ (%d files)" % copied)

    # 2. register everything with the build
    t = conf.read_text(errors="surrogateescape")
    added = []
    for path in CONF_NEW:
        if path in t:
            continue
        line = path + "\t\t\tstandard"
        done = False
        for anchor in CONF_LINES:
            if anchor in t:
                t = t.replace(anchor, anchor + "\n" + line, 1)
                done = True
                break
        if not done:
            t = t.rstrip("\n") + "\n" + line + "\n"
        added.append(path)
    if added:
        conf.write_text(t, errors="surrogateescape")
        print("patch-pandora-gpu-probe: bsd/conf/files += %s" % ", ".join(added))

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
    g = gpu.read_text(errors="surrogateescape")
    if MARK not in g:
        gpu.write_text("/* %s */\n" % MARK + g, encoding="utf-8")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
