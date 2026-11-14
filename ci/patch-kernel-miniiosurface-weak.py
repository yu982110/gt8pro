#!/usr/bin/env python3
"""patch-kernel-miniiosurface-weak.py -- let the tree link with MiniIOSurface.cpp out of the build.

ci/patch-kernel-display.py replaces the port's Mini display drivers with Sweet's
(SweetSurface/SweetFramebuffer), because both define the same IOKit classes.  The port's own
osfmk/arm64/loader_return.c still declares and calls

    extern void MiniIOSurfaceUsage(uint32_t *count, uint64_t *bytes);
    ...
    MiniIOSurfaceUsage(&surfaces, &surface_bytes);

so the kernel fails to link with exactly one undefined symbol:

    Undefined symbols for architecture arm64:
      "_MiniIOSurfaceUsage", referenced from: ...

Making the declaration weak and guarding the call keeps the log line when the function exists
(the plain build) and skips it when it does not (the display build).

    python3 ci/patch-kernel-miniiosurface-weak.py <xnu source tree>
"""
import pathlib
import sys

FILE = "osfmk/arm64/loader_return.c"
MARK = "HAKIOS-MINI-WEAK"

OLD_DECL = "extern void MiniIOSurfaceUsage(uint32_t *count, uint64_t *bytes);"
NEW_DECL = ("/* " + MARK + ": MiniIOSurface.cpp is not in the display build, so the symbol may be absent. */\n"
            "extern void MiniIOSurfaceUsage(uint32_t *count, uint64_t *bytes) __attribute__((weak));")


def main(root):
    p = pathlib.Path(root) / FILE
    if not p.exists():
        print("patch-miniiosurface-weak: MISS: no", FILE)
        return 1
    text = p.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-miniiosurface-weak: already applied")
        return 0
    if OLD_DECL not in text:
        print("patch-miniiosurface-weak: MISS: the declaration is not the one this patch knows")
        return 1
    text = text.replace(OLD_DECL, NEW_DECL, 1)
    # guard every call
    n = 0
    out = []
    for line in text.split("\n"):
        if "MiniIOSurfaceUsage(&" in line and "if (" not in line:
            indent = line[:len(line) - len(line.lstrip())]
            out.append(indent + "if (MiniIOSurfaceUsage != 0) {")
            out.append(line)
            out.append(indent + "}")
            n += 1
        else:
            out.append(line)
    p.write_text("\n".join(out), errors="surrogateescape")
    print(f"patch-miniiosurface-weak: weak declaration, {n} call(s) guarded")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: patch-kernel-miniiosurface-weak.py <xnu source tree>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
