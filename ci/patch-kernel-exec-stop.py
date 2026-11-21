#!/usr/bin/env python3
"""Stop just before and just after load_machfile, from kern_exec.c.

Why here and not in mach_loader.c: every build that touched mach_loader.c came up
silent -- not one byte in the kernel's log ring, the heartbeat timer never firing.  The
one build that talks is the original probe, which writes a panic into grade_binary.  So
the observation has to happen from a file that is not mach_loader.c, and the two points
that matter are either side of the call:

    hakios_stop_at=11   exec_mach_imgact, just before  load_machfile()
    hakios_stop_at=12   exec_mach_imgact, just after   load_machfile() returned

If 11 fires and 12 does not, the hang is inside load_machfile.  If both fire, the image is
loaded and the hang is in what follows.  panic is the only channel that reaches the log.

usage: patch-kernel-exec-stop.py <xnu source dir>
"""
import pathlib
import sys

MARK = "HAKIOS-EXEC-STOP"

HELPER = """
/* HAKIOS-EXEC-STOP: see ci/patch-kernel-exec-stop.py -- panic at exec stage N on request. */
static void
hakios_exec_stop(int stage, const char *what)
{
\tint want = 0;

\tPE_parse_boot_argn("hakios_stop_at", &want, sizeof(want));
\tif (want == stage) {
\t\tpanic("HAKIOS-EXEC-STOP %d at %s (kern_exec.c)", stage, what);
\t}
}
"""

BEFORE_OLD = """\t/*
\t * Actually load the image file we previously decided to load.
\t */
\tlret = load_machfile(imgp, mach_header, thread, &map, &load_result);
"""
BEFORE_NEW = """\t/*
\t * Actually load the image file we previously decided to load.
\t */
\thakios_exec_stop(11, "before load_machfile");
\tlret = load_machfile(imgp, mach_header, thread, &map, &load_result);
\thakios_exec_stop(12, "after load_machfile");
"""

INC_OLD = "#include <sys/param.h>\n"
INC_NEW = "#include <sys/param.h>\n#include <pexpert/pexpert.h> /* HAKIOS-EXEC-STOP: PE_parse_boot_argn */\n"


def main(root):
    f = pathlib.Path(root) / "bsd/kern/kern_exec.c"
    if not f.exists():
        print("patch-exec-stop: bsd/kern/kern_exec.c not found")
        return 1
    t = f.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-exec-stop: already there")
        return 0
    rc = 0
    if "pexpert/pexpert.h" not in t:
        if INC_OLD in t:
            t = t.replace(INC_OLD, INC_NEW, 1)
        else:
            print("patch-exec-stop: WARN include anchor missing")
            rc = 1
    anchor = "exec_mach_imgact(struct image_params *imgp)"
    if anchor not in t:
        print("patch-exec-stop: MISS exec_mach_imgact")
        return 1
    # the function has the return type on the line before; put the helper before that
    i = t.index(anchor)
    line_start = t.rfind("\n", 0, i) + 1
    prev_line_start = t.rfind("\n", 0, line_start - 1) + 1
    t = t[:prev_line_start] + HELPER.strip("\n") + "\n\n" + t[prev_line_start:]
    if BEFORE_OLD not in t:
        print("patch-exec-stop: MISS load_machfile call site")
        return 1
    t = t.replace(BEFORE_OLD, BEFORE_NEW, 1)
    print("patch-exec-stop: probes 11/12 around load_machfile are in")
    f.write_text(t, errors="surrogateescape")
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
