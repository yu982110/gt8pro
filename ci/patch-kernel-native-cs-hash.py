#!/usr/bin/env python3
"""patch-kernel-native-cs-hash.py -- let the kernel verify the shared cache with its own hashes.

launchd died with:

    libignition: goodbye: ignition sequence complete          <- the gate passed
    dyld cache '(null)' not loaded: code signature registration for shared cache failed
    Library not loaded: /usr/lib/libSystem.B.dylib

The dyld shared cache is signed over 16 KiB pages, because that is the page size Apple's phones
have. Our kernel now runs 4 KiB pages, because this SoC cannot translate 16 KiB ones, so the
precomputed hashes in the cache no longer match what the kernel computes and registration fails
-- and with the cache unregistered, every dylib looks missing.

XNU can compute the hashes itself instead of trusting the precomputed table; the define that
turns that on is XNU_NATIVE_CS_HASH_CACHE. The same-SoC-family reference sets it for its board:

    MACHINE_FLAGS_ARM64_PANDORA = ... -DPANDORA=1 -DXNU_NATIVE_CS_HASH_CACHE=1 -march=...

This appends it to every arm64 machine flag line in makedefs/MakeInc.def, so whichever board our
build picks gets it too.

    python3 ci/patch-kernel-native-cs-hash.py <xnu tree>
"""
import pathlib
import re
import sys

MARK = "HAKIOS-LAFA-NATIVE-CS-HASH"
DEFINE = "-DXNU_NATIVE_CS_HASH_CACHE=1"


def main(root):
    f = pathlib.Path(root) / "makedefs/MakeInc.def"
    if not f.exists():
        print(f"patch-native-cs-hash: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-native-cs-hash: already applied")
        return 0

    # CFLAGS_ARM64 is the one every arm64 compile sees; MACHINE_FLAGS_ARM64_<BOARD> only covers the
    # boards listed there (ours is not one of them), which is why the first attempt changed nothing.
    count = 0

    def add(m):
        nonlocal count
        line = m.group(0)
        if DEFINE in line:
            return line
        count += 1
        return line.replace("-DLP64", f"-DLP64 {DEFINE}", 1)

    text = re.sub(r"^CFLAGS_ARM64\t= .*$", add, text, flags=re.M)
    text = re.sub(r"^CFLAGS_ARM64 = .*$", add, text, flags=re.M)
    if count == 0:
        print("patch-native-cs-hash: MISS: no CFLAGS_ARM64 lines to change")
        return 1

    text += (
        "\n# " + MARK + ": the dyld shared cache is signed over 16 KiB pages (Apple's page size);\n"
        "# this kernel runs 4 KiB pages, so it has to compute the hashes itself.\n"
    )
    f.write_text(text, errors="surrogateescape")
    print(f"patch-native-cs-hash: {count} board flag line(s) now define {DEFINE}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-native-cs-hash.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
