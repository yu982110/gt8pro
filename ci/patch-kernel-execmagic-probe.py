#!/usr/bin/env python3
"""Print the Mach-O magic and filetype of the image exec is about to reject.

load_init_program ends with `failed loading /sbin/launchd: errno 8` (ENOEXEC) and the
LOAD_BADMACHO probe never fired, so the reject happens before load_machfile -- in
exec_mach_imgact itself, which has exactly two ENOEXEC paths for a plain file:

    bsd/kern/kern_exec.c:1194   magic != MH_MAGIC && magic != MH_MAGIC_64    -> error = -1
    bsd/kern/kern_exec.c:1200   filetype != MH_EXECUTE                       -> error = -1

(and EBADARCH at :1188 for a byte-swapped magic).  The line is written through
loader_log_line() so it lands in the loader's live ring, not only on the console.

usage: patch-kernel-execmagic-probe.py <xnu source dir>
"""
import pathlib
import sys

MARK = "HAKIOS-EXECMAGIC"

ANCHOR = "\tchar *                  userspace_coredump_name = NULL;\n"
ADD = (
    "\tchar *                  userspace_coredump_name = NULL;\n"
    "\n"
    "\textern void loader_log_line(const char *line);\n"
    "\n"
    "\t/*\n"
    "\t * " + MARK + ": what did exec actually read?  See ci/patch-kernel-execmagic-probe.py.\n"
    "\t */\n"
    "\t{\n"
    "\t\tchar lafa_em[200];\n"
    "\n"
    "\t\tsnprintf(lafa_em, sizeof(lafa_em),\n"
    "\t\t    \"HAKIOS-EXECMAGIC %s magic %x filetype %x cputype %x sub %x ncmds %x\\n\",\n"
    "\t\t    imgp->ip_ndp->ni_cnd.cn_pnbuf, (unsigned)mach_header->magic,\n"
    "\t\t    (unsigned)mach_header->filetype, (unsigned)mach_header->cputype,\n"
    "\t\t    (unsigned)mach_header->cpusubtype, (unsigned)mach_header->ncmds);\n"
    "\t\tloader_log_line(lafa_em);\n"
    "\t}\n"
)


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
    print("patch-execmagic-probe: exec_mach_imgact prints the magic into the live ring")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
