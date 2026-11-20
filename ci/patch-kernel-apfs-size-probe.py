#!/usr/bin/env python3
"""Panic with the APFS size chain's values, so they reach the ring.

Where this stands: the kernel mounts the APFS root, finds /sbin/launchd, and refuses to
exec it --

    panic: Process 1 exec of /sbin/launchd failed, errno 8 @kern_exec.c:7295

-- errno 8 being ENOEXEC, which bsd/kern/kern_exec.c:6266 returns for a zero-length file.
The value comes from apfs_vnop_getattr()'s va_data_size, taken from an->an_size, which
apfs_node_vnode() set from apfs_inode_size() (the decmpfs header's uncompressed size, or
the inode's DSTREAM size).

The earlier attempt logged this through loader_log_line(), which writes into the kernel's
in-memory log buffer -- the on-device ring is only written when the kernel returns to the
loader, and a DB_HALT panic never returns.  This version panics instead, with the values in
the panic text: the panic text is what the loader writes down, and the debugger halts right
there, so the numbers are readable from the ring.  It fires once, on the first regular
file's getattr, which is /sbin/launchd.

    python3 ci/patch-kernel-apfs-size-probe.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-APFSSIZE"

GETATTR_OLD = "\tino = an->an_ino;\n\tsize = an->an_size;\n"

GETATTR_NEW = (
    "\tino = an->an_ino;\n"
    "\tsize = an->an_size;\n"
    "\t/* HAKIOS-APFSSIZE: the value bsd/kern/kern_exec.c's ENOEXEC test sees.  A panic, not\n"
    "\t * the loader's log: that log only reaches the ring when the kernel returns to the\n"
    "\t * loader, and a DB_HALT panic never does.  The panic text is what gets written down. */\n"
    "\tif ((ino.mode & APFS_S_IFMT) != APFS_S_IFDIR) {\n"
    "\t\tstatic int hakios_apfssize_shown;\n"
    "\t\tif (hakios_apfssize_shown < 1) {\n"
    "\t\t\thakios_apfssize_shown++;\n"
    "\t\t\tpanic(\"APFSSIZE id %llu an_size %llu ino_size %llu mode %x\",\n"
    "\t\t\t    (unsigned long long)an->an_id, (unsigned long long)an->an_size,\n"
    "\t\t\t    (unsigned long long)ino.size, (unsigned)ino.mode);\n"
    "\t\t}\n"
    "\t}\n"
)


def main(root):
    p = pathlib.Path(root) / "bsd/miscfs/apfs/apfs_vnops.c"
    if not p.exists():
        print(f"patch-apfs-size-probe: {p} not found")
        return 1
    t = p.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-apfs-size-probe: already applied")
        return 0
    if GETATTR_OLD not in t:
        print("patch-apfs-size-probe: MISS: apfs_vnop_getattr is not the expected shape")
        return 1
    t = t.replace(GETATTR_OLD, GETATTR_NEW, 1)
    p.write_text(t, errors="surrogateescape")
    print("patch-apfs-size-probe: apfs_vnop_getattr panics with the size values")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
