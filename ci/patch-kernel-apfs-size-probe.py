#!/usr/bin/env python3
"""Log what APFS tells the kernel about a file's size, right where the kernel reads it.

State: the kernel mounts the APFS root, finds /sbin/launchd, and then refuses to exec it:

    panic: Process 1 exec of /sbin/launchd failed, errno 8 @kern_exec.c:7295

errno 8 is ENOEXEC, which bsd/kern/kern_exec.c:6266 returns for exactly one reason:

    /* Disallow zero length files */
    if (vap->va_data_size == 0) return ENOEXEC;

So the value has to come from APFS getattr: apfs_vnop_getattr() fills va_data_size from
an->an_size, which apfs_node_vnode() set from apfs_inode_size() (either the decmpfs
header's uncompressed size or the inode's DSTREAM size).  This patch logs both ends into
the loader's ring -- the only log that survives a panic on this phone -- for the first
few files of each kind, then stops, so the ring cannot be flooded.

    python3 ci/patch-kernel-apfs-size-probe.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-APFSSIZE"

PROTO = """/* HAKIOS-APFSSIZE */
#include <libkern/libkern.h>
extern void loader_log_line(const char *line);
"""

# --- apfs_vnop_getattr: what the kernel's getattr is told ------------------------------
GETATTR_OLD = """	ino = an->an_ino;
	size = an->an_size;
"""

GETATTR_NEW = """	ino = an->an_ino;
	size = an->an_size;
	/* HAKIOS-APFSSIZE: the value bsd/kern/kern_exec.c's ENOEXEC test will see */
	{
		static int gshown;
		if (gshown < 24) {
			char line[160];
			gshown++;
			snprintf(line, sizeof(line),
			    "HAKIOS-APFSSIZE getattr id %llu an_size %llu ino_size %llu\\n",
			    (unsigned long long)an->an_id, (unsigned long long)an->an_size,
			    (unsigned long long)ino.size);
			loader_log_line(line);
		}
	}
"""

# --- apfs_inode_size: the two values it chooses between ------------------------------
SIZE_OLD = """	/* only the decmpfs header is needed here, not the table of pieces */
	error = cfile_header(vol, ino, &compressed, &type, &csize, &in_rsrc);
"""

SIZE_NEW = """	/* HAKIOS-APFSSIZE: log before anything returns early (a directory has no decmpfs
	 * header and cfile_header returns ENOENT for it) */
	{
		static int shown;
		if (shown < 24) {
			char line[160];
			shown++;
			snprintf(line, sizeof(line),
			    "HAKIOS-APFSSIZE inode_size obj %llu mode %x ino_size %llu\\n",
			    (unsigned long long)ino->obj_id, (unsigned)ino->mode,
			    (unsigned long long)ino->size);
			loader_log_line(line);
		}
	}
	/* only the decmpfs header is needed here, not the table of pieces */
	error = cfile_header(vol, ino, &compressed, &type, &csize, &in_rsrc);
"""


def insert_proto(text):
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if l.startswith("#include"):
            lines.insert(i + 1, PROTO)
            break
    return "\n".join(lines)


def patch_one(path, old, new, what):
    p = pathlib.Path(path)
    if not p.exists():
        print(f"patch-apfs-size-probe: {p} not found")
        return 1
    t = p.read_text(errors="surrogateescape")
    if MARK in t:
        print(f"patch-apfs-size-probe: {what} already has the probe")
        return 0
    if old not in t:
        print(f"patch-apfs-size-probe: MISS: {what} is not the expected shape")
        return 1
    t = insert_proto(t).replace(old, new, 1)
    p.write_text(t, errors="surrogateescape")
    print(f"patch-apfs-size-probe: {what} now logs into the ring")
    return 0


def main(root):
    tree = pathlib.Path(root)
    rc = patch_one(tree / "bsd/miscfs/apfs/apfs_vnops.c", GETATTR_OLD, GETATTR_NEW,
                   "apfs_vnop_getattr")
    rc |= patch_one(tree / "bsd/miscfs/apfs/apfs_lib.c", SIZE_OLD, SIZE_NEW,
                    "apfs_inode_size")
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
