#!/usr/bin/env python3
"""HAKIOS-LAFA-CSLOG: log what a named process's code signatures do (0284 from the reference).

The userland jobs die with SIGBUS/SIGABRT and the only hint is "Library not loaded" -- we cannot
tell whether the signature of a mapped image was added, rejected, or never reached.  The
same-SoC reference solved the same question with 0284-cslog: boot-arg

    pandora_cslog=<process name>

makes ubc_cs_blob_add() print every signature that process adds, with its result:

    PANDORA CS: SpringBoard adds the signature of /System/Library/.../SpringBoard: 0

0 is success; 6/10 are the signature errors.  The print goes through the console path, so it is
readable with the usual log.

Idempotent.

    python3 ci/patch-kernel-cslog.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-CSLOG"

FUNC = '''
#include <pexpert/pexpert.h>

/* %s: boot-arg pandora_cslog=NAME logs each code signature a process of that name
 * adds (the images dyld maps outside the shared cache), with the result. */
static void
hakios_cslog(struct vnode *vp, int error)
{
\tstatic int state;                       /* 0 not read yet, 1 off, 2 on */
\tstatic char want[32];

\tif (state == 0) {
\t\tstate = PE_parse_boot_arg_str("pandora_cslog", want, sizeof(want)) && want[0] ? 2 : 1;
\t}
\tif (state != 2) {
\t\treturn;
\t}
\tchar name[32];
\tproc_selfname(name, sizeof(name));
\tif (strncmp(name, want, sizeof(name)) != 0) {
\t\treturn;
\t}
\tchar path[256];
\tint len = sizeof(path);
\tif (vn_getpath(vp, path, &len) != 0) {
\t\tstrlcpy(path, "?", sizeof(path));
\t}
\tprintf("PANDORA CS: %%s adds the signature of %%s: %%d\\n", name, path, error);
}

''' % MARK


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    f = root / "bsd/kern/ubc_subr.c"
    if not f.exists():
        print("patch-cslog: MISS bsd/kern/ubc_subr.c")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-cslog: already applied")
        return 0

    anchor = "int\nubc_cs_blob_add(\n"
    if anchor not in text:
        print("patch-cslog: MISS ubc_cs_blob_add definition")
        return 1
    text = text.replace(anchor, FUNC + anchor, 1)

    # the call, at the function's out: label
    call = "\tout:\n"
    start = text.index("ubc_cs_blob_add(")
    pos = text.find(call, start)
    if pos < 0:
        print("patch-cslog: MISS out: label in ubc_cs_blob_add")
        return 1
    pos += len(call)
    # skip to the first statement after the label, if it is a declaration/assignment
    text = text[:pos] + "\thakios_cslog(vp, error);   /* %s */\n" % MARK + text[pos:]
    f.write_text(text, errors="surrogateescape")
    print("patch-cslog: signature logging added (boot-arg pandora_cslog=NAME)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
