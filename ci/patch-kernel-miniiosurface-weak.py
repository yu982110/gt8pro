#!/usr/bin/env python3
"""patch-kernel-miniiosurface-weak.py -- let the tree link with MiniIOSurface.cpp out of the build.

ci/patch-kernel-display.py replaces the port's Mini display drivers with Sweet's
(SweetSurface/SweetFramebuffer), because both define the same IOKit classes.  The port's own
osfmk/arm64/loader_return.c still declares and calls

    extern void MiniIOSurfaceUsage(uint32_t *count, uint64_t *bytes);
    ...
    MiniIOSurfaceUsage(&surfaces, &surface_bytes);      (inside hakios_memory_line)

so the kernel fails to link with exactly one undefined symbol:

    Undefined symbols for architecture arm64:
      "_MiniIOSurfaceUsage", referenced from:
      _hakios_memory_line in loader_return.o

A weak declaration is not enough: this kernel links with undefined weak imports fatal.  The call only
adds surface counts to a log line, so the log line drops that part when the function is not compiled
in -- the numbers it would print come from SweetSurface.cpp's own accounting.

    python3 ci/patch-kernel-miniiosurface-weak.py <xnu source tree>
"""
import pathlib
import sys

FILE = "osfmk/arm64/loader_return.c"
MARK = "HAKIOS-MINI-OUT"

OLD_DECL = "extern void MiniIOSurfaceUsage(uint32_t *count, uint64_t *bytes);"


def main(root):
    p = pathlib.Path(root) / FILE
    if not p.exists():
        print("patch-miniiosurface-out: MISS: no", FILE)
        return 1
    text = p.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-miniiosurface-out: already applied")
        return 0
    if OLD_DECL not in text:
        print("patch-miniiosurface-out: MISS: the declaration is not the one this patch knows")
        return 1
    text = text.replace(
        OLD_DECL,
        "/* " + MARK + ": MiniIOSurface.cpp is not in the display build; the surface counts below\n"
        " * come from SweetSurface.cpp instead, so this declaration and its call are left out. */",
        1)
    out = []
    n = 0
    for line in text.split("\n"):
        if "MiniIOSurfaceUsage(&" in line and not line.lstrip().startswith("/*"):
            indent = line[:len(line) - len(line.lstrip())]
            out.append(indent + "/* " + MARK + ": " + line.strip() + " -- not in this build */")
            n += 1
        else:
            out.append(line)
    p.write_text("\n".join(out), errors="surrogateescape")
    print(f"patch-miniiosurface-out: declaration replaced, {n} call(s) commented out")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: patch-kernel-miniiosurface-weak.py <xnu source tree>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
