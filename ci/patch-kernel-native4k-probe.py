#!/usr/bin/env python3
"""patch-kernel-native4k-probe.py -- say what the native-4K hash path is doing.

The kernel runs 4 KiB pages and has the UniXNU native-4K signature hash cache compiled in
(_cs_native4k_prepare is in the binary), but dyld still cannot register the shared cache:

    dyld cache '(null)' not loaded: code signature registration for shared cache failed

cs_native4k_prepare returns early on its first guard when the blob does not need converting:

    if (blob->csb_hash_pageshift <= PAGE_SHIFT) {
            return 0;                       <- "nothing to do"
    }

If the shared cache's blob reports a hash page shift of 12 (4 KiB) rather than 14 (16 KiB), the
function does nothing and the cache keeps Apple's 16 KiB hashes. This prints the blob's fields and
what the function returns, so one boot says whether that guard is the problem.

The prints go through loader_log_line(), not kprintf(): it writes into the loader's own ring
buffer, which the loader draws on screen and persists to hybridswap. That makes an absent print
mean "not called" -- with kprintf the log has to be read back from the disk afterwards, and that
ring mixes several boots together, so absence proves nothing.

    python3 ci/patch-kernel-native4k-probe.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-NATIVE4K-PROBE"

# declaration goes before the noinline attribute, not between it and the function
FN_ANCHOR = """__attribute__((noinline))
static int
cs_native4k_prepare(vnode_t vp, struct cs_blob *blob)
"""

FN_NEW = """/* HAKIOS-NATIVE4K-PROBE: loader_log_line writes to the loader's ring, which is on screen. */
#include <libkern/libkern.h>
extern void loader_log_line(const char *line);

__attribute__((noinline))
static int
cs_native4k_prepare(vnode_t vp, struct cs_blob *blob)
"""

# function's declarations end at kr; then the first guard. Insert both prints here, so every
# declaration still comes before any statement in the function body.
BODY_ANCHOR = """	kern_return_t kr;

	if (blob->csb_hash_pageshift <= PAGE_SHIFT) {
		return 0;
	}
"""

BODY_NEW = """	kern_return_t kr;

	{
		/* HAKIOS-NATIVE4K-PROBE: what this blob looks like before the guard decides. */
		char probe_buf[160];

		snprintf(probe_buf, sizeof(probe_buf),
		    "probe: n4k enter hash_ps %u fl %u base %lld mem %llu page_ps %u\\n",
		    (unsigned)blob->csb_hash_pageshift, (unsigned)blob->csb_hash_firstlevel_pageshift,
		    (long long)blob->csb_base_offset, (unsigned long long)blob->csb_mem_size,
		    (unsigned)PAGE_SHIFT);
		loader_log_line(probe_buf);
	}
	if (blob->csb_hash_pageshift <= PAGE_SHIFT) {
		/* HAKIOS-NATIVE4K-PROBE: the first guard said "nothing to do". */
		char probe_buf[96];

		snprintf(probe_buf, sizeof(probe_buf), "probe: n4k skipped %u le %u\\n",
		    (unsigned)blob->csb_hash_pageshift, (unsigned)PAGE_SHIFT);
		loader_log_line(probe_buf);
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
	{
		/* HAKIOS-NATIVE4K-PROBE: what came out of the conversion. */
		char probe_buf[128];

		snprintf(probe_buf, sizeof(probe_buf), "probe: n4k result %d count %llu ready %lu\\n",
		    error, (unsigned long long)blob->csb_native4k_hash_count,
		    cs_native4k_ready_blobs);
		loader_log_line(probe_buf);
	}
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
        (FN_ANCHOR, FN_NEW, "declaration"),
        (BODY_ANCHOR, BODY_NEW, "entry + guard prints"),
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
