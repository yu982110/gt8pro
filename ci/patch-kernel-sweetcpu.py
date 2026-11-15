#!/usr/bin/env python3
"""HAKIOS-LAFA-CPU: install the reference port's SweetCPU (IOCPU + PSCI CPU_ON).

The kernel has the IOCPU framework (100 matches) and PSCI code (22) but *no* IOCPU
driver, so it can never start a secondary core: with cpus=8 it dies before the ring log
even starts.  The reference port solved this with iokit/Kernel/arm/SweetCPU.cpp, which is
built up across its patch series (0018 boot CPU, 0065 SMP/PSCI, 0078 eight cores).

This script installs the composed final file (ci/hakios/SweetCPU.cpp), lists it in
iokit/conf/files.arm64, and adds its personality to gIOKernelConfigTables.

    python3 ci/patch-kernel-sweetcpu.py <xnu tree>

Idempotent; exits 0 when all three steps are in place.
"""
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "hakios" / "SweetCPU.cpp"
DEST_REL = "iokit/Kernel/arm/SweetCPU.cpp"
FILES_LINE = "iokit/Kernel/arm/SweetCPU.cpp                                optional iokitcpp\n"
MARK = "HAKIOS-LAFA-CPU"

TABLE_BLOCK = (
    "#if defined(__arm64__)\n"
    "    /* " + MARK + ": the IOCPU driver that brings the other seven cores up. */\n"
    '    "   ,{"\n'
    "    \"     'IOClass'         = SweetCPU;\"\n"
    "    \"     'IOProviderClass' = IOPlatformDevice;\"\n"
    "    \"     'IONameMatch'     = 'qcom,kryo470';\"\n"
    "    \"     'IOProbeScore'    = 10000:32;\"\n"
    '    "   }"\n'
    "#endif\n"
)


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-sweetcpu.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    ok = 0

    if not SRC.exists():
        print("patch-sweetcpu: MISS ci/hakios/SweetCPU.cpp")
        return 1

    dest = root / DEST_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.read_bytes() == SRC.read_bytes():
        print("patch-sweetcpu: SweetCPU.cpp already installed")
    else:
        dest.write_bytes(SRC.read_bytes())
        print("patch-sweetcpu: installed iokit/Kernel/arm/SweetCPU.cpp")
    ok += 1

    f = root / "iokit/conf/files.arm64"
    if f.exists():
        text = f.read_text(encoding="utf-8", errors="replace")
        if DEST_REL in text:
            print("patch-sweetcpu: files.arm64 already lists SweetCPU.cpp")
        else:
            if not text.endswith("\n"):
                text += "\n"
            text += FILES_LINE
            f.write_text(text, encoding="utf-8")
            print("patch-sweetcpu: SweetCPU.cpp added to files.arm64")
        ok += 1
    else:
        print("patch-sweetcpu: MISS iokit/conf/files.arm64")

    c = root / "iokit/KernelConfigTables.cpp"
    if c.exists():
        text = c.read_text(encoding="utf-8", errors="replace")
        if MARK in text:
            print("patch-sweetcpu: personality already present")
            ok += 1
        else:
            matches = list(re.finditer(r'^[ \t]*"\)";[ \t]*$', text, re.MULTILINE))
            if matches:
                at = matches[-1].start()
                text = text[:at] + TABLE_BLOCK + text[at:]
                c.write_text(text, encoding="utf-8")
                print("patch-sweetcpu: SweetCPU personality added to gIOKernelConfigTables")
                ok += 1
            else:
                print('patch-sweetcpu: MISS the closing ")"; in KernelConfigTables.cpp')
    else:
        print("patch-sweetcpu: MISS iokit/KernelConfigTables.cpp")

    return 0 if ok == 3 else 1


if __name__ == "__main__":
    sys.exit(main())
