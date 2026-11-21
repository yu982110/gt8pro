#!/usr/bin/env python3
"""patch-kernel-pandora-iokit.py -- the Adreno 840 user client for this kernel's IOKit.

Installs data-a-live's PandoraAdrenoGpu.cpp (NativeAdreno's user-client ABI v1, what Turnip's
Darwin backend opens) and wires it the way the reference port does (xnu patch 0177):

  iokit/Kernel/arm/PandoraAdrenoGpu.cpp     the client (talks to bsd/dev/pandora_gpu.c's
                                            exported pandora_gpu_* functions, level 14)
  iokit/Kernel/arm/NativeAdrenoABIv1.h      the ABI header
  iokit/conf/files.arm64                    + one line, optional iokitcpp
  iokit/KernelConfigTables.cpp              + a match entry: IOClass PandoraAdrenoGpu on
                                            IOResources, match category PandoraAdrenoGpu

Boot-arg pandora_gpu_metal=1 publishes a second service (AGXMetalG14X, for NativeMetal); without
it only PandoraAdrenoGpu exists, which is what Turnip takes
(TU_DARWIN_SERVICE=PandoraAdrenoGpu TU_DARWIN_OPEN_TYPE=0x4147).

    python3 ci/patch-kernel-pandora-iokit.py <hakios tree>
"""
import pathlib
import sys

MARK = "HAKIOS-PANDORA-IOKIT"
HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "iokit" / "PandoraAdrenoGpu.cpp"
HDR = HERE / "iokit" / "NativeAdrenoABIv1.h"

CONF_LINE = "iokit/Kernel/arm/PandoraAdrenoGpu.cpp                           optional iokitcpp"
CONF_ANCHORS = [
    "iokit/Kernel/arm/SweetFramebuffer.cpp",
    "iokit/Kernel/arm/FuxiAdrenoUserClient.cpp",
]
CONF_TABLE = "iokit/KernelConfigTables.cpp"
TABLE_ANCHOR = "    \"   {\"\n    \"     'IOClass'         = IOWatchdog;\"\n"
TABLE_ADD = ("    \"   {\"\n"
             "    \"     'IOClass'         = PandoraAdrenoGpu;\"\n"
             "    \"     'IOProviderClass' = IOResources;\"\n"
             "    \"     'IOMatchCategory' = PandoraAdrenoGpu;\"\n"
             "    \"   },\"\n"
             "    \"   {\"\n"
             "    \"     'IOClass'         = IOWatchdog;\"\n")


def main(root):
    root = pathlib.Path(root)
    dst = root / "iokit/Kernel/arm/PandoraAdrenoGpu.cpp"
    hdr = root / "iokit/Kernel/arm/NativeAdrenoABIv1.h"
    conf = root / "iokit/conf/files.arm64"
    table = root / CONF_TABLE

    for p in (SRC, HDR):
        if not p.exists():
            print("patch-pandora-iokit: %s missing (run tools/port-iokit.sh)" % p)
            return 1
    for p in (conf, table):
        if not p.exists():
            print("patch-pandora-iokit: %s not found" % p)
            return 1

    dst.parent.mkdir(parents=True, exist_ok=True)
    body = SRC.read_text(encoding="utf-8")
    if MARK not in body:
        body = "/* %s */\n" % MARK + body
    dst.write_text(body, encoding="utf-8")
    hdr.write_text(HDR.read_text(encoding="utf-8"), encoding="utf-8")
    print("patch-pandora-iokit: iokit/Kernel/arm/PandoraAdrenoGpu.cpp (%d lines)"
          % len(body.splitlines()))

    t = conf.read_text(errors="surrogateescape")
    if "PandoraAdrenoGpu.cpp" not in t:
        done = False
        for anchor in CONF_ANCHORS:
            if anchor in t:
                # put it right after the last line that starts with that anchor
                idx = t.index(anchor)
                eol = t.index("\n", idx)
                t = t[:eol + 1] + CONF_LINE + "\n" + t[eol + 1:]
                done = True
                break
        if not done:
            t = t.rstrip("\n") + "\n" + CONF_LINE + "\n"
        conf.write_text(t, errors="surrogateescape")
        print("patch-pandora-iokit: iokit/conf/files.arm64 += PandoraAdrenoGpu.cpp")
    else:
        print("patch-pandora-iokit: iokit/conf/files.arm64 already lists it")

    t = table.read_text(errors="surrogateescape")
    if "PandoraAdrenoGpu" in t:
        print("patch-pandora-iokit: KernelConfigTables.cpp already has the entry")
    elif TABLE_ANCHOR in t:
        t = t.replace(TABLE_ANCHOR, TABLE_ADD, 1)
        table.write_text(t, errors="surrogateescape")
        print("patch-pandora-iokit: KernelConfigTables.cpp += PandoraAdrenoGpu on IOResources")
    else:
        print("patch-pandora-iokit: MISS KernelConfigTables anchor (IOWatchdog entry)")
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
