#!/usr/bin/env python3
"""Print the Mach-O magic and filetype of the image exec is about to reject.

load_init_program ends with `failed loading /sbin/launchd: errno 8` (ENOEXEC) and the
LOAD_BADMACHO probe never fired, so the reject happens before load_machfile -- in
exec_mach_imgact itself, which has exactly two ENOEXEC paths for a plain file:

    bsd/kern/kern_exec.c:1194   magic != MH_MAGIC && magic != MH_MAGIC_64    -> error = -1
    bsd/kern/kern_exec.c:1200   filetype != MH_EXECUTE                       -> error = -1

(and EBADARCH at :1188 for a byte-swapped magic).  kprintf works in this build, so print what
the kernel actually read: magic, filetype, and the first word of the data buffer.

usage: patch-kernel-execmagic-probe.py <xnu source dir>
"""
import pathlib
import sys

MARK = "HAKIOS-EXECMAGIC"

ANCHOR = "\tchar *                  userspace_coredump_name = NULL;\n"
ADD = """\tchar *                  userspace_coredump_name = NULL;

\t/*
\t * HAKIOS-EXECMAGIC: what did exec actually read?  load_init_program reports errno 8 for
\t * /sbin/launchd and the LOAD_BADMACHO probe does not fire, so the reject is one of the
\t * magic/filetype tests just below.  See ci/patch-kernel-execmagic-probe.py.
\t */
\tprintf("HAKIOS-EXECMAGIC %s magic %x filetype %x cputype %x sub %x ncmds %x\\n",
\t    imgp->ip_ndp->ni_cnd.cn_pnbuf, (unsigned)mach_header->magic,
\t    (unsigned)mach_header->filetype, (unsigned)mach_header->cputype,
\t    (unsigned)mach_header->cpusubtype, (unsigned)mach_header->ncmds);
"""


def main(root):
    p = pathlib.Path(root) / "bsd/kern/kern_exec.c"
    if not p.exists():
        print("patch-execmagic-probe: %s not found" % p)
        return 1
    t = p.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-execmagic-probe: already applied")
        return 0
    if t.count(ANCHOR) != 1:
        print("patch-execmagic-probe: MISS anchor (found %d)" % t.count(ANCHOR))
        return 1
    p.write_text(t.replace(ANCHOR, ADD, 1), errors="surrogateescape")
    print("patch-execmagic-probe: exec_mach_imgact now prints magic/filetype")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
