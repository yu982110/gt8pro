#!/usr/bin/env python3
"""Name the value that makes /sbin/launchd look zero bytes long.

The kernel now finds /sbin/launchd on the APFS root and refuses to exec it:

    panic: Process 1 exec of /sbin/launchd failed, errno 8 @kern_exec.c:7295

errno 8 is ENOEXEC, and bsd/kern/kern_exec.c:6266 returns it for one reason:

    /* Disallow zero length files */
    if (vap->va_data_size == 0) return ENOEXEC;

so the APFS getattr reported size 0.  The chain is:

    apfs_vnop_getattr   apfs_vnops.c:366   size = an->an_size
    apfs_node_vnode     apfs_vfsops.c:557  apfs_inode_size(&vol, &ino, &size)
    apfs_inode_size     apfs_lib.c:1615    *size = compressed ? csize : ino->size

Either the decmpfs header's uncompressed size came back 0, or the inode's DSTREAM size
did.  This patch logs all of it into the loader's ring (the only log that survives a
panic on this phone), for the first few inodes only, so the ring is not flooded.

    python3 ci/patch-kernel-apfs-size-probe.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-APFSSIZE"

PROTO = """/* HAKIOS-APFSSIZE */
#include <libkern/libkern.h>
extern void loader_log_line(const char *line);
"""

OLD = """	/* only the decmpfs header is needed here, not the table of pieces */
	error = cfile_header(vol, ino, &compressed, &type, &csize, &in_rsrc);
	if (error) {
		return error;
	}
	*size = compressed ? csize : ino->size;
	return 0;
"""

NEW = """	/* only the decmpfs header is needed here, not the table of pieces */
	error = cfile_header(vol, ino, &compressed, &type, &csize, &in_rsrc);
	if (error) {
		return error;
	}
	*size = compressed ? csize : ino->size;
	/* HAKIOS-APFSSIZE: the first few files only; the ring is small and a panic is the only
	 * way this log is ever read. */
	{
		static int shown;
		if (shown < 12) {
			char line[160];
			shown++;
			snprintf(line, sizeof(line),
			    "HAKIOS-APFSSIZE obj %llu mode %x comp %d type %x csize %llu ino_size %llu out %llu\\n",
			    (unsigned long long)ino->obj_id, (unsigned)ino->mode, (int)compressed,
			    (unsigned)type, (unsigned long long)csize,
			    (unsigned long long)ino->size, (unsigned long long)*size);
			loader_log_line(line);
		}
	}
	return 0;
"""


def main(root):
    p = pathlib.Path(root) / "bsd/miscfs/apfs/apfs_lib.c"
    if not p.exists():
        print(f"patch-apfs-size-probe: {p} not found")
        return 1
    t = p.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-apfs-size-probe: already applied")
        return 0
    if OLD not in t:
        print("patch-apfs-size-probe: MISS: apfs_inode_size is not the expected shape")
        return 1
    # the prototypes go after the first include block
    lines = t.split("\n")
    for i, l in enumerate(lines):
        if l.startswith("#include"):
            lines.insert(i + 1, PROTO)
            break
    t = "\n".join(lines).replace(OLD, NEW, 1)
    p.write_text(t, errors="surrogateescape")
    print("patch-apfs-size-probe: apfs_inode_size now logs into the ring")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
