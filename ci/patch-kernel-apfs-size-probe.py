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
    "\t/* HAKIOS-APFSSIZE: the Mach-O header the kernel actually read (with mount) */\n"
    "\t{\n"
    "\t\tstatic int mshown;\n"
    "\t\tif (mshown < 1) {\n"
    "\t\t\t/* HAKIOS-APFSSIZE: is the root the memory-backed mockfs (and over which\n"
    "\t\t\t * physical region), or a static fake tree? Copy of struct mockfs_mount's\n"
    "\t\t\t * first fields: root, memory_backed, physical_memory, memdev_base, memdev_size. */\n"
    "\t\t\tstruct hakios_mockfs_probe {\n"
    "\t\t\t\tvoid *hp_root;\n"
    "\t\t\t\tint hp_memory_backed;\n"
    "\t\t\t\tint hp_physical_memory;\n"
    "\t\t\t\tuint64_t hp_memdev_base;\n"
    "\t\t\t\tuint64_t hp_memdev_size;\n"
    "\t\t\t};\n"
    "\t\t\tmount_t kmp = imgp->ip_vp ? vnode_mount(imgp->ip_vp) : NULL;\n"
    "\t\t\tstruct hakios_mockfs_probe *hmp = kmp ? (struct hakios_mockfs_probe *)kmp->mnt_data : NULL;\n"
    "\t\t\tvnode_t kvp = imgp->ip_vp;\n"
    "\t\t\tmshown++;\n"
    "\t\t\tpanic(\"MACHHDR magic %x ftype %d cpu %x sub %x ncmds %u size %llu fs %s tag %u from %s mem %d base %llu msize %llu vdata %p vname %s\",\n"
    "\t\t\t    (unsigned)mach_header->magic, (int)mach_header->filetype,\n"
    "\t\t\t    (unsigned)mach_header->cputype, (unsigned)mach_header->cpusubtype,\n"
    "\t\t\t    (unsigned)mach_header->ncmds, (unsigned long long)imgp->ip_arch_size,\n"
    "\t\t\t    kvp ? vfs_statfs(vnode_mount(kvp))->f_fstypename : \"(null)\",\n"
    "\t\t\t    kvp ? (unsigned)vnode_tag(kvp) : 0,\n"
    "\t\t\t    kmp ? vfs_statfs(kmp)->f_mntfromname : \"(null)\",\n"
    "\t\t\t    hmp ? hmp->hp_memory_backed : -1,\n"
    "\t\t\t    hmp ? (unsigned long long)hmp->hp_memdev_base : 0,\n"
    "\t\t\t    hmp ? (unsigned long long)hmp->hp_memdev_size : 0,\n"
    "\t\t\t    kvp ? kvp->v_data : NULL,\n"
    "\t\t\t    kvp ? (kvp->v_name ? kvp->v_name : \"(noname)\") : \"(null)\");\n"
    "\t\t}\n"
    "\t}\n"
    "\t/* HAKIOS-APFSSIZE: the header IS a Mach-O -- so mockfs served the right bytes.\n"
    "\t * Say so and stop: with the small root image (oem ramdisk + stage) this is the\n"
    "\t * proof that the read path is sound and the remaining work is user space. */\n"
    "\tif ((mach_header->magic == MH_MAGIC) || (mach_header->magic == MH_MAGIC_64)) {\n"
    "\t\tmount_t gmp = imgp->ip_vp ? vnode_mount(imgp->ip_vp) : NULL;\n"
    "\t\tvnode_t gvp = imgp->ip_vp;\n"
    "\t\tpanic(\"GOODHDR magic %x ftype %d cpu %x sub %x ncmds %u size %llu fs %s from %s vname %s\",\n"
    "\t\t    (unsigned)mach_header->magic, (int)mach_header->filetype,\n"
    "\t\t    (unsigned)mach_header->cputype, (unsigned)mach_header->cpusubtype,\n"
    "\t\t    (unsigned)mach_header->ncmds, (unsigned long long)imgp->ip_arch_size,\n"
    "\t\t    gvp ? vfs_statfs(vnode_mount(gvp))->f_fstypename : \"(null)\",\n"
    "\t\t    gmp ? vfs_statfs(gmp)->f_mntfromname : \"(null)\",\n"
    "\t\t    gvp ? (gvp->v_name ? gvp->v_name : \"(noname)\") : \"(null)\");\n"
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




# --- apfs_lib.c: is the inode record itself garbage? ---------------------------------
INODE_OLD = "\t\tino->rdev = le32(xf);\n\t}\n\treturn 0;\n}\n"

INODE_NEW = (
    "\t\tino->rdev = le32(xf);\n"
    "\t}\n"
    "\t/* HAKIOS-APFSSIZE: the inode record as parsed; if these look like garbage the\n"
    "\t * problem is the B-tree walk, not the file read. */\n"
    "\t{\n"
    "\t\tstatic int ishown;\n"
    "\t\tif (ishown < 1) {\n"
    "\t\t\tishown++;\n"
    "\t\t\tpanic(\"APFSINO id %llu parent %llu mode %x size %llu nch %d dstream %d\",\n"
    "\t\t\t    (unsigned long long)ino->obj_id, (unsigned long long)ino->parent_id,\n"
    "\t\t\t    (unsigned)ino->mode, (unsigned long long)ino->size,\n"
    "\t\t\t    (int)ino->nchildren, (int)ino->has_dstream);\n"
    "\t\t}\n"
    "\t}\n"
    "\treturn 0;\n"
    "}\n"
)


def patch_inode(root):
    f = pathlib.Path(root) / "bsd/miscfs/apfs/apfs_lib.c"
    if not f.exists():
        print("patch-apfs-size-probe: apfs_lib.c not found")
        return 1
    t2 = f.read_text(errors="surrogateescape")
    if "HAKIOS-APFSSIZE: the inode record as parsed" in t2:
        print("patch-apfs-size-probe: apfs_lib.c already has the inode panic")
        return 0
    if INODE_OLD not in t2:
        print("patch-apfs-size-probe: MISS: apfs_inode_get is not the expected shape")
        return 1
    f.write_text(t2.replace(INODE_OLD, INODE_NEW, 1), errors="surrogateescape")
    print("patch-apfs-size-probe: apfs_lib.c panics with the parsed inode")
    return 0




# --- apfs_lib.c cursor_next: the record as the B-tree walk produced it ---------------
REC_OLD = "\t\t\trec->obj_id = jkey_id(k);\n\t\t\trec->type = jkey_type(k);\n\t\t\treturn 0;\n"

REC_NEW = (
    "\t\t\trec->obj_id = jkey_id(k);\n"
    "\t\t\trec->type = jkey_type(k);\n"
    "\t\t\t/* HAKIOS-APFSSIZE: the record the B-tree walk produced; garbage here means the\n"
    "\t\t\t * node entry offsets or the node layout are wrong, not the file read. */\n"
    "\t\t\t{\n"
    "\t\t\t\tstatic int rshown;\n"
    "\t\t\t\tif (rshown < 3) {\n"
    "\t\t\t\t\trshown++;\n"
    "\t\t\t\t\tpanic(\"APFSREC obj %llu type %x key_len %u val_len %u\",\n"
    "\t\t\t\t\t    (unsigned long long)rec->obj_id, (unsigned)rec->type,\n"
    "\t\t\t\t\t    (unsigned)rec->key_len, (unsigned)rec->val_len);\n"
    "\t\t\t\t\t}\n"
    "\t\t\t}\n"
    "\t\t\treturn 0;\n"
)


def patch_rec(root):
    f = pathlib.Path(root) / "bsd/miscfs/apfs/apfs_lib.c"
    if not f.exists():
        print("patch-apfs-size-probe: apfs_lib.c not found")
        return 1
    t2 = f.read_text(errors="surrogateescape")
    if "HAKIOS-APFSSIZE: the record the B-tree" in t2:
        print("patch-apfs-size-probe: cursor_next already has the record panic")
        return 0
    if REC_OLD not in t2:
        print("patch-apfs-size-probe: MISS: apfs_cursor_next is not the expected shape")
        return 1
    f.write_text(t2.replace(REC_OLD, REC_NEW, 1), errors="surrogateescape")
    print("patch-apfs-size-probe: cursor_next panics with the record")
    return 0




# --- apfs_vnops.c vnop_read: what the read path believes about the file --------------
READ_OLD = "\tino = APFS_VP(vp)->an_ino;\n\tsize = APFS_VP(vp)->an_size;\n"

READ_NEW = (
    "\tino = APFS_VP(vp)->an_ino;\n"
    "\tsize = APFS_VP(vp)->an_size;\n"
    "\t/* HAKIOS-APFSSIZE: the read path's view of the file -- the inode it was built from\n"
    "\t * and the size, plus the first bytes the caller asked for. */\n"
    "\tif (!vnode_isdir(vp)) {\n"
    "\t\tstatic int rshown;\n"
    "\t\tif (rshown < 2) {\n"
    "\t\t\trshown++;\n"
    "\t\t\tpanic(\"APFSREAD obj %llu ino_size %llu an_size %llu mode %x\",\n"
    "\t\t\t    (unsigned long long)ino.obj_id, (unsigned long long)ino.size,\n"
    "\t\t\t    (unsigned long long)size, (unsigned)ino.mode);\n"
    "\t\t}\n"
    "\t}\n"
)


def patch_read(root):
    f = pathlib.Path(root) / "bsd/miscfs/apfs/apfs_vnops.c"
    if not f.exists():
        print("patch-apfs-size-probe: apfs_vnops.c not found")
        return 1
    t2 = f.read_text(errors="surrogateescape")
    if "HAKIOS-APFSSIZE: the read path" in t2:
        print("patch-apfs-size-probe: vnop_read already has the probe")
        return 0
    if READ_OLD not in t2:
        print("patch-apfs-size-probe: MISS: apfs_vnop_read is not the expected shape")
        return 1
    f.write_text(t2.replace(READ_OLD, READ_NEW, 1), errors="surrogateescape")
    print("patch-apfs-size-probe: apfs_vnop_read panics with the read path's values")
    return 0




# --- apfs_lib.c inode_size: the two candidate sizes -----------------------------------
SIZE2_OLD = "\t*size = compressed ? csize : ino->size;\n\treturn 0;\n}\n"

SIZE2_NEW = (
    "\t*size = compressed ? csize : ino->size;\n"
    "\t/* HAKIOS-APFSSIZE: which of the two candidates is garbage? */\n"
    "\t{\n"
    "\t\tstatic int s2shown;\n"
    "\t\tif (s2shown < 2) {\n"
    "\t\t\ts2shown++;\n"
    "\t\t\tpanic(\"APFSSIZE2 obj %llu mode %x ino_size %llu comp %d csize %llu out %llu\",\n"
    "\t\t\t    (unsigned long long)ino->obj_id, (unsigned)ino->mode,\n"
    "\t\t\t    (unsigned long long)ino->size, (int)compressed,\n"
    "\t\t\t    (unsigned long long)csize, (unsigned long long)*size);\n"
    "\t\t}\n"
    "\t}\n"
    "\treturn 0;\n"
    "}\n"
)


def patch_size2(root):
    f = pathlib.Path(root) / "bsd/miscfs/apfs/apfs_lib.c"
    if not f.exists():
        print("patch-apfs-size-probe: apfs_lib.c not found")
        return 1
    t2 = f.read_text(errors="surrogateescape")
    if "HAKIOS-APFSSIZE: which of the two candidates" in t2:
        print("patch-apfs-size-probe: apfs_inode_size already has the size2 panic")
        return 0
    if SIZE2_OLD not in t2:
        print("patch-apfs-size-probe: MISS: apfs_inode_size is not the expected shape")
        return 1
    f.write_text(t2.replace(SIZE2_OLD, SIZE2_NEW, 1), errors="surrogateescape")
    print("patch-apfs-size-probe: apfs_inode_size panics with both size candidates")
    return 0


def main(root):
    # HAKIOS-PROBE-OFF: the probe printed a VALID Mach-O header
    # (feedfacf / 2 / 100000c / 0 / ncmds 8 / size 49152 / fs mockfs): the exec path is
    # sound and the probe only stopped the boot, so it stays out of the way.  The body is
    # kept as _probe_main() for when a probe is wanted again.
    print("patch-apfs-size-probe: disabled (the Mach-O header is correct)")
    return 0


def _probe_main(root):
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
    rc |= patch_inode(root)
    rc |= patch_rec(root)
    rc |= patch_read(root)
    rc |= patch_size2(root)
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
