#!/usr/bin/env python3
"""Stop at a named point in load_machfile and panic, so the loader gets the log.

What is known already: the kernel reaches grade_binary (the probe's panic there fires
every time and its text lands in the log), and nothing after it is ever seen -- no
output, no init, and the heartbeat timer never fires, which means the CPU is inside a
loop with interrupts off or a spinlock held.  `load_machfile` is the next stretch of
code after grade_binary.

printf cannot be used to look: on this phone the kernel's printf has no visible sink.
panic can, and the loader writes the panic text to the last megabyte of hybridswap where
Android can read it.  So each probe point panics on request:

    hakios_stop_at=1   the entry of load_machfile
    hakios_stop_at=2   after grade_binary
    hakios_stop_at=3   just before load_machfile returns its map

One kernel answers all three: each panic returns to the loader, which can run again.

usage: patch-kernel-stop-probe.py <xnu source dir>
"""
import pathlib
import sys

MARK = "HAKIOS-STOP"

HELPER = """
/* HAKIOS-STOP: see ci/patch-kernel-stop-probe.py -- panic at load stage N on request.
 * The loader turns a panic into a log on the disk; nothing else is visible here. */
static void
hakios_stop_at(int stage, const char *what)
{
\tint want = 0;

\tPE_parse_boot_argn("hakios_stop_at", &want, sizeof(want));
\tif (want == stage) {
\t\tpanic("HAKIOS-STOP %d at %s (mach_loader.c)", stage, what);
\t}
}
"""

# 1: the entry of load_machfile, right after its declarations
ENTRY_OLD = """\tunsigned int            pmap_flags = 0;

\tif (os_add_overflow(file_offset, macho_size, &total_size) ||
"""
ENTRY_NEW = """\tunsigned int            pmap_flags = 0;

\thakios_stop_at(1, "load_machfile entry");

\tif (os_add_overflow(file_offset, macho_size, &total_size) ||
"""

# 2: after grade_binary has accepted the binary
GRADE_OLD = """\tif (!grade_binary(header->cputype,
\t    header->cpusubtype & ~CPU_SUBTYPE_MASK,
\t    header->cpusubtype & CPU_SUBTYPE_MASK, TRUE)) {
\t\treturn LOAD_BADARCH;
\t}

\tabi64 = ((header->cputype & CPU_ARCH_ABI64) == CPU_ARCH_ABI64);
"""
GRADE_NEW = """\tif (!grade_binary(header->cputype,
\t    header->cpusubtype & ~CPU_SUBTYPE_MASK,
\t    header->cpusubtype & CPU_SUBTYPE_MASK, TRUE)) {
\t\treturn LOAD_BADARCH;
\t}

\thakios_stop_at(2, "after grade_binary");

\tabi64 = ((header->cputype & CPU_ARCH_ABI64) == CPU_ARCH_ABI64);
"""

# 3: just before the map is handed back
END_OLD = """\t}
\t*mapp = map;
"""
END_NEW = """\t}
\thakios_stop_at(3, "load_machfile end, before *mapp = map");
\t*mapp = map;
"""

# the helper needs the include for PE_parse_boot_argn
INC_OLD = "#include <sys/param.h>\n"
INC_NEW = "#include <sys/param.h>\n#include <pexpert/pexpert.h> /* HAKIOS-STOP: PE_parse_boot_argn */\n"


def main(root):
    f = pathlib.Path(root) / "bsd/kern/mach_loader.c"
    if not f.exists():
        print("patch-stop-probe: bsd/kern/mach_loader.c not found")
        return 1
    t = f.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-stop-probe: already there")
        return 0
    rc = 0
    # include, if not already pulled in
    if "pexpert/pexpert.h" not in t:
        if INC_OLD in t:
            t = t.replace(INC_OLD, INC_NEW, 1)
        else:
            print("patch-stop-probe: WARN include anchor missing")
            rc = 1
    # the helper goes right before the function
    anchor = "load_machfile(\n\tstruct image_params     *imgp,"
    if anchor not in t:
        print("patch-stop-probe: MISS load_machfile signature")
        return 1
    t = t.replace(anchor, HELPER.strip("\n") + "\n\nstatic load_return_t\n" + anchor, 1)
    for old, new, name in ((ENTRY_OLD, ENTRY_NEW, "entry"),
                           (GRADE_OLD, GRADE_NEW, "after grade_binary"),
                           (END_OLD, END_NEW, "end")):
        if old not in t:
            print("patch-stop-probe: MISS %s anchor" % name)
            rc = 1
            continue
        t = t.replace(old, new, 1)
        print("patch-stop-probe: probe %s in" % name)
    f.write_text(t, errors="surrogateescape")
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
