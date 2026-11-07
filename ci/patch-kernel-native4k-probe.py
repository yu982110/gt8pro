#!/usr/bin/env python3
"""patch-kernel-native4k-probe.py -- say what the native-4K hash path is doing.

The kernel now runs 4 KiB pages and has the UniXNU native-4K signature hash cache compiled in
(_cs_native4k_prepare is in the binary), but dyld still cannot register the shared cache:

    dyld cache '(null)' not loaded: code signature registration for shared cache failed
    Library not loaded: /usr/lib/libSystem.B.dylib

One run of the self-test even passed all 36 checks, so the path works sometimes.

cs_native4k_prepare returns early on its first guard when the blob does not need converting:

    if (blob->csb_hash_pageshift <= PAGE_SHIFT) {
            return 0;                       <- "nothing to do"
    }

If the shared cache's blob reports a hash page shift of 12 (4 KiB) rather than 14 (16 KiB), the
function does nothing and the cache keeps Apple's 16 KiB hashes. This probe prints the fields, and
prints what the function returns, so one boot says whether that guard is the problem.

    python3 ci/patch-kernel-native4k-probe.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-NATIVE4K-PROBE"

ENTRY_ANCHOR = """static int
cs_native4k_prepare(vnode_t vp, struct cs_blob *blob)
{
	const CS_CodeDirectory *cd = blob->csb_cd;
"""

ENTRY_NEW = """static int
cs_native4k_prepare(vnode_t vp, struct cs_blob *blob)
{
	/* HAKIOS-NATIVE4K-PROBE: what this blob looks like before the guard decides. */
	kprintf("hakios native4k: enter hash_pageshift=%u firstlevel=%u base_off=%lld mem_size=%llu pageshift=%u\\n",
	    (unsigned)blob->csb_hash_pageshift, (unsigned)blob->csb_hash_firstlevel_pageshift,
	    (long long)blob->csb_base_offset, (unsigned long long)blob->csb_mem_size,
	    (unsigned)PAGE_SHIFT);
	const CS_CodeDirectory *cd = blob->csb_cd;
"""

# the early return from the first guard
GUARD_ANCHOR = """	if (blob->csb_hash_pageshift <= PAGE_SHIFT) {
		return 0;
	}
"""

GUARD_NEW = """	if (blob->csb_hash_pageshift <= PAGE_SHIFT) {
		/* HAKIOS-NATIVE4K-PROBE: the first guard said "nothing to do". */
		kprintf("hakios native4k: skipped (hash_pageshift %u <= %u)\\n",
		    (unsigned)blob->csb_hash_pageshift, (unsigned)PAGE_SHIFT);
		return 0;
	}
"""

RETURN_ANCHOR = """out:
	kfree_data(buffer, 16384);
	if (storage) {
		kmem_free(kernel_map, storage, allocation);
	}
	return error;
}
"""

RETURN_NEW = """out:
	kfree_data(buffer, 16384);
	if (storage) {
		kmem_free(kernel_map, storage, allocation);
	}
	/* HAKIOS-NATIVE4K-PROBE: what came out of the conversion. */
	kprintf("hakios native4k: result %d, count %llu, ready blobs %lu\\n",
	    error, (unsigned long long)blob->csb_native4k_hash_count,
	    cs_native4k_ready_blobs);
	return error;
}
"""


def main(root):
    f = pathlib.Path(root) / "bsd/kern/ubc_subr.c"
    if not f.exists():
        print(f"patch-native4k-probe: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-native4k-probe: already applied")
        return 0

    bad = 0
    for anchor, new, what in (
        (ENTRY_ANCHOR, ENTRY_NEW, "entry print"),
        (GUARD_ANCHOR, GUARD_NEW, "first-guard print"),
        (RETURN_ANCHOR, RETURN_NEW, "result print"),
    ):
        if anchor in text:
            text = text.replace(anchor, new, 1)
            print(f"patch-native4k-probe: {what}")
        else:
            print(f"patch-native4k-probe: MISS: {what} (anchor not found)")
            bad = 1

    f.write_text(text, errors="surrogateescape")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-native4k-probe.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
