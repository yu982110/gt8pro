#!/usr/bin/env python3
"""patch-kernel-cs-skipbig.py -- do not hash the dyld shared cache at all, for now.

The 2.7 GB dyld shared cache is hashed page by page when dyld registers it
(F_ADDFILESIGS_RETURN -> cs_blob_create -> cs_native4k_prepare). That walk keeps the whole file
in the UBC page cache, and inside our memory window there is no room for it beside the resident
image and the user-space mapping of the same file: the kernel dies silently at dyld's
"dynamic linking" step every time the cache is present.

The reference port's answer is IO_NOCACHE plus 1 MiB reads (see patch-kernel-cs-nocache.py). This
is the blunter variant, kept as a step to take first: for a signed file over 32 MiB, report
success without hashing anything. Then check whether dyld and launchd get further, and only then
come back and make the hash real and cheap (IO_NOCACHE, or hashing page hashes lazily).

    python3 ci/patch-kernel-cs-skipbig.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-SKIPBIG"

ANCHOR = """	for (chunk = 0; chunk < limit; chunk += bufsize) {
"""

NEW = """	/* HAKIOS-SKIPBIG: a signed file over 32 MiB is the dyld shared cache (2.7 GB). Hashing
	 * it reads the whole file into the page cache, which does not fit in the memory window
	 * beside the resident image and the same mapping user space is about to make. Say yes
	 * without hashing; the pages are then not validated on fault until this is made real. */
	if (limit > (32ULL << 20)) {
		printf("HAKIOS: skipping the hash of a %llu byte signed file\\n",
		    (unsigned long long)limit);
		error = 0;
		goto out;
	}
"""


def main(root):
    f = pathlib.Path(root) / "bsd/kern/ubc_subr.c"
    if not f.exists():
        print(f"patch-cs-skipbig: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-cs-skipbig: already applied")
        return 0
    if ANCHOR not in text:
        print("patch-cs-skipbig: MISS: the 0127 read loop is not where expected")
        return 1
    text = text.replace(ANCHOR, NEW + ANCHOR, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-cs-skipbig: signed files over 32 MiB are not hashed")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-cs-skipbig.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
