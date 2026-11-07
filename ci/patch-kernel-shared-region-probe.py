#!/usr/bin/env python3
"""patch-kernel-shared-region-probe.py -- say which half of the shared region mapping fails.

dyld cannot register the dyld shared cache:

    dyld[4]: dyld cache '(null)' not loaded: code signature registration for shared cache failed

and it never gets as far as the signature work -- the native-4K conversion probe in
cs_blob_create never prints, which means the blob it would have converted was never created.

_shared_region_map_and_slide() has only two places that can fail:

    error = shared_region_map_and_slide_setup(...);      <- validation, vnode lookups, the AMFI
                                                            trust-cache check on line 2439
    if (error != 0) return error;

    kr = vm_shared_region_map_file(...);                 <- the actual mapping
    if (kr != KERN_SUCCESS) { trace... }

The prints go through loader_log_line(), not kprintf(): it writes into the loader's own ring
buffer, which is what the loader draws on screen and persists to hybridswap, so the numbers are
visible on the spot instead of needing a reboot into Android to read the kernel log.

    python3 ci/patch-kernel-shared-region-probe.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-SR-PROBE"

FN_ANCHOR = """int
_shared_region_map_and_slide(
"""

FN_NEW = """/* HAKIOS-SR-PROBE: loader_log_line writes to the loader's ring, which is on screen. */
#include <libkern/libkern.h>
extern void loader_log_line(const char *line);

int
_shared_region_map_and_slide(
"""

ENTRY_ANCHOR = """	/*
	 * Turn files, mappings into sr_file_mappings and other setup.
	 */
	error = shared_region_map_and_slide_setup(p, files_count,
	    files, mappings_count, mappings,
	    &sr_file_mappings, &shared_region, rdir_vp);
	if (error != 0) {
"""

ENTRY_NEW = """	/*
	 * Turn files, mappings into sr_file_mappings and other setup.
	 */
	{
		/* HAKIOS-SR-PROBE: the syscall was reached at all. */
		char probe_buf[128];

		snprintf(probe_buf, sizeof(probe_buf), "probe: sr entry pid %d files %u maps %u\\n",
		    proc_getpid(p), files_count, mappings_count);
		loader_log_line(probe_buf);
	}
	error = shared_region_map_and_slide_setup(p, files_count,
	    files, mappings_count, mappings,
	    &sr_file_mappings, &shared_region, rdir_vp);
	{
		char probe_buf[96];

		snprintf(probe_buf, sizeof(probe_buf), "probe: sr setup -> %d\\n", error);
		loader_log_line(probe_buf);
	}
	if (error != 0) {
"""

MAP_ANCHOR = """	kr = vm_shared_region_map_file(shared_region, files_count, sr_file_mappings);
	if (kr != KERN_SUCCESS) {
"""

MAP_NEW = """	kr = vm_shared_region_map_file(shared_region, files_count, sr_file_mappings);
	{
		/* HAKIOS-SR-PROBE: 0 would mean the cache is mapped. */
		char probe_buf[96];

		snprintf(probe_buf, sizeof(probe_buf), "probe: sr map_file -> 0x%x\\n", (unsigned)kr);
		loader_log_line(probe_buf);
	}
	if (kr != KERN_SUCCESS) {
"""


def main(root):
    f = pathlib.Path(root) / "bsd/vm/vm_unix.c"
    if not f.exists():
        print(f"patch-sr-probe: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-sr-probe: already applied")
        return 0

    bad = 0
    for anchor, new, what in (
        (FN_ANCHOR, FN_NEW, "declaration"),
        (ENTRY_ANCHOR, ENTRY_NEW, "entry + setup prints"),
        (MAP_ANCHOR, MAP_NEW, "map_file print"),
    ):
        if anchor in text:
            text = text.replace(anchor, new, 1)
            print(f"patch-sr-probe: {what}")
        else:
            print(f"patch-sr-probe: MISS: {what} (anchor not found)")
            bad = 1

    f.write_text(text, errors="surrogateescape")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-shared-region-probe.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
