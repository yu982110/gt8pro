#!/usr/bin/env python3
"""patch-kernel-cs-nocache.py -- do not fill the page cache while hashing a huge signed file.

The reference port (PLAN.md, UniXNU 477e530) has two halves to "boot reads the dyld shared cache
and fills memory": read signature-covered files in 1 MiB chunks (patch 0127) and open files over
32 MiB with IO_NOCACHE so their bytes do not stay in the page cache.

The second half is what our kernel lacks. cs_native4k_prepare() walks the whole file through
vn_rdwr() to check every page's hash; for the 2.7 GB dyld shared cache that keeps 2.7 GB in the
UBC page cache on top of the resident image, inside a window that must also hold the user space
about to map the same 2.7 GB. The kernel dies silently at dyld's "dynamic linking" step exactly
when the cache is present.

The change is one expression in the vn_rdwr() call so nothing else in the function moves.

    python3 ci/patch-kernel-cs-nocache.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-CS-NOCACHE"

ANCHOR = """		error = vn_rdwr(UIO_READ, vp, (caddr_t)buffer, (int)chunk_length, position,
		    UIO_SYSSPACE, 0, vfs_context_ucred(vfs_context_current()),
"""

NEW = """		/* HAKIOS-CS-NOCACHE: a signed file over 32 MiB (the dyld shared cache, 2.7 GB)
		 * is hashed without keeping its bytes in the page cache: they do not fit beside
		 * the resident image and the mapping user space is about to make of the same file. */
		error = vn_rdwr(UIO_READ, vp, (caddr_t)buffer, (int)chunk_length, position,
		    UIO_SYSSPACE, (limit > (32ULL << 20)) ? IO_NOCACHE : 0,
		    vfs_context_ucred(vfs_context_current()),
"""


def main(root):
    f = pathlib.Path(root) / "bsd/kern/ubc_subr.c"
    if not f.exists():
        print(f"patch-cs-nocache: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-cs-nocache: already applied")
        return 0
    if ANCHOR not in text:
        print("patch-cs-nocache: MISS: the 0127 read loop is not where expected")
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-cs-nocache: hashing files over 32 MiB no longer fills the page cache")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-cs-nocache.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
