#!/usr/bin/env python3
"""patch-kernel-cs-nocache.py -- do not fill the page cache while hashing a huge signed file.

The reference port (PLAN.md, UniXNU 477e530) says the fix for "boot reads the dyld shared cache
and fills memory" has two halves: read signature-covered files in 1 MiB chunks (patch 0127) and
open files larger than 32 MiB with IO_NOCACHE so their contents do not stay in the page cache.

The second half is what our kernel lacks. cs_native4k_prepare() walks the whole file through
vn_rdwr() to check every page's hash; for the 2.7 GB dyld shared cache that means 2.7 GB of UBC
page cache on top of the resident image, inside a memory window that also holds the kernel and
the user space that is about to map the same 2.7 GB again. The kernel dies silently at dyld's
"dynamic linking" step every time the cache is present - this is the memory it runs out of.

    python3 ci/patch-kernel-cs-nocache.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-CS-NOCACHE"

ANCHOR = """		error = vn_rdwr(UIO_READ, vp, (caddr_t)buffer, (int)chunk_length, position,
		    UIO_SYSSPACE, 0, vfs_context_ucred(vfs_context_current()),
"""

NEW = """		/*
		 * HAKIOS-CS-NOCACHE: a signed file bigger than 32 MiB is hashed without keeping
		 * its bytes in the page cache. The dyld shared cache is 2.7 GB; cached, it does
		 * not fit beside the kernel and the same 2.7 GB the user space is about to map.
		 */
		{
			static unsigned hakios_nocache_reported;
			int hakios_ioflag = (limit > (32ULL << 20)) ? IO_NOCACHE : 0;

			if (hakios_ioflag != 0 && hakios_nocache_reported < 4) {
				hakios_nocache_reported++;
				printf("HAKIOS: cs hash of %llu bytes with IO_NOCACHE\\n", (unsigned long long)limit);
			}
			error = vn_rdwr(UIO_READ, vp, (caddr_t)buffer, (int)chunk_length, position,
			    UIO_SYSSPACE, hakios_ioflag, vfs_context_ucred(vfs_context_current()),
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
        print("patch-cs-nocache: MISS: cs_native4k_prepare read loop not found "
              "(is 0127 applied?)")
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
