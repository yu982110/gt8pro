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




# ------------------------------------------------------------------------------------------------
# And the consumer end: bsd/kern/kern_exec.c's exec_check_permissions() is the code that returns
# ENOEXEC for a zero-length file -- it says so in its own comment.  Panic there with everything
# that test can see, so the ring names the source of the zero.
EXEC_OLD = """	/* Disallow zero length files */
	if (vap->va_data_size == 0) {
		return ENOEXEC;
	}
"""

EXEC_NEW = """	/* Disallow zero length files */
	if (vap->va_data_size == 0) {
		/* HAKIOS-APFSSIZE: the ENOEXEC site.  A panic, so the values reach the ring. */
		panic("EXECZERO start %llu size %llu alloc %llu size2 %llu type %d mode %x",
		    (unsigned long long)vap->va_fileid, (unsigned long long)vap->va_data_size,
		    (unsigned long long)vap->va_data_alloc, (unsigned long long)vap->va_total_size,
		    (int)vap->va_type, (unsigned)vap->va_mode);
		return ENOEXEC;
	}
"""


def patch_exec(root):
    f = pathlib.Path(root) / "bsd/kern/kern_exec.c"
    if not f.exists():
        print("patch-apfs-size-probe: kern_exec.c not found")
        return 1
    t2 = f.read_text(errors="surrogateescape")
    if "HAKIOS-APFSSIZE: the ENOEXEC site" in t2:
        print("patch-apfs-size-probe: kern_exec.c already has the panic")
        return 0
    if EXEC_OLD not in t2:
        print("patch-apfs-size-probe: MISS: exec_check_permissions is not the expected shape")
        return 1
    f.write_text(t2.replace(EXEC_OLD, EXEC_NEW, 1), errors="surrogateescape")
    print("patch-apfs-size-probe: kern_exec.c panics at the ENOEXEC site")
    return 0




# --- apfs_vfsops.c: the size the vnode is created with --------------------------------
VNODE_OLD = "\tvfsp.vnfs_filesize = size;\n"

VNODE_NEW = (
    "\t/* HAKIOS-APFSSIZE: the size apfs_node_vnode is about to give this vnode */\n"
    "\tif (vtype != VDIR) {\n"
    "\t\tstatic int vshown;\n"
    "\t\tif (vshown < 1) {\n"
    "\t\t\tvshown++;\n"
    "\t\t\tpanic(\"APFSSIZE vnode id %llu type %d size %llu ino_size %llu\",\n"
    "\t\t\t    (unsigned long long)obj_id, (int)vtype, (unsigned long long)size,\n"
    "\t\t\t    (unsigned long long)ino.size);\n"
    "\t\t}\n"
    "\t}\n"
    "\tvfsp.vnfs_filesize = size;\n"
)


def patch_vnode(root):
    f = pathlib.Path(root) / "bsd/miscfs/apfs/apfs_vfsops.c"
    if not f.exists():
        print("patch-apfs-size-probe: apfs_vfsops.c not found")
        return 1
    t2 = f.read_text(errors="surrogateescape")
    if "HAKIOS-APFSSIZE: the size apfs_node_vnode" in t2:
        print("patch-apfs-size-probe: apfs_vfsops.c already has the panic")
        return 0
    if VNODE_OLD not in t2:
        print("patch-apfs-size-probe: MISS: apfs_vfsops.c vnfs_filesize is not the expected shape")
        return 1
    f.write_text(t2.replace(VNODE_OLD, VNODE_NEW, 1), errors="surrogateescape")
    print("patch-apfs-size-probe: apfs_vfsops.c panics with the vnode size")
    return 0


# --- kern_exec.c grade_binary: what the kernel actually read -------------------------
MACH_OLD = "\tif ((mach_header->magic != MH_MAGIC) &&\n\t    (mach_header->magic != MH_MAGIC_64)) {\n"

MACH_NEW = (
    "\t/* HAKIOS-APFSSIZE: the Mach-O header the kernel actually read */\n"
    "\t{\n"
    "\t\tstatic int mshown;\n"
    "\t\tif (mshown < 1) {\n"
    "\t\t\tmshown++;\n"
    "\t\t\tpanic(\"MACHHDR magic %x filetype %d cputype %x cpusub %x ncmds %u size %llu\",\n"
    "\t\t\t    (unsigned)mach_header->magic, (int)mach_header->filetype,\n"
    "\t\t\t    (unsigned)mach_header->cputype, (unsigned)mach_header->cpusubtype,\n"
    "\t\t\t    (unsigned)mach_header->ncmds, (unsigned long long)imgp->ip_arch_size);\n"
    "\t\t}\n"
    "\t}\n"
    "\tif ((mach_header->magic != MH_MAGIC) &&\n"
    "\t    (mach_header->magic != MH_MAGIC_64)) {\n"
)


def patch_mach(root):
    f = pathlib.Path(root) / "bsd/kern/kern_exec.c"
    if not f.exists():
        print("patch-apfs-size-probe: kern_exec.c not found")
        return 1
    t2 = f.read_text(errors="surrogateescape")
    if "HAKIOS-APFSSIZE: the Mach-O header" in t2:
        print("patch-apfs-size-probe: kern_exec.c already has the mach header panic")
        return 0
    if MACH_OLD not in t2:
        print("patch-apfs-size-probe: MISS: grade_binary header check is not the expected shape")
        return 1
    f.write_text(t2.replace(MACH_OLD, MACH_NEW, 1), errors="surrogateescape")
    print("patch-apfs-size-probe: kern_exec.c panics with the Mach-O header")
    return 0


def main(root):
    p = pathlib.Path(root) / "bsd/miscfs/apfs/apfs_vnops.c"
    if not p.exists():
        print(f"patch-apfs-size-probe: {p} not found")
        return 1
    t = p.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-apfs-size-probe: apfs_vnop_getattr already has the probe")
    else:
        if GETATTR_OLD not in t:
            print("patch-apfs-size-probe: MISS: apfs_vnop_getattr is not the expected shape")
            return 1
        t = t.replace(GETATTR_OLD, GETATTR_NEW, 1)
        p.write_text(t, errors="surrogateescape")
        print("patch-apfs-size-probe: apfs_vnop_getattr panics with the size values")
    rc = patch_exec(root)
    rc |= patch_vnode(root)
    rc |= patch_mach(root)
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
