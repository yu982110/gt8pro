#!/usr/bin/env python3
"""patch-kernel-native4k-early.py -- run the native-4K hash conversion before the early returns.

The 0105 patch (UniXNU's native 4K signature hash cache) puts its call at the end of
cs_blob_create, after ~20 returns:

    error = cs_native4k_prepare(vp, &tmp_blob);      <- line ~4858, unreachable for some blobs
    if (error != 0) goto out;

A probe in that function never printed, so for the dyld shared cache the blob returns before
reaching it -- cs_blob_create has early returns at cs_blob_init_validated (L4608), at
reconstitute_code_signature (L4619), at mac_vnode_check_signature (L4679), and a dozen more.
The shared cache is exactly the case the conversion is for: its blob carries Apple's 16 KiB
hashes, which this 4 KiB kernel cannot match.

This moves the conversion to just after the blob's fields are filled in (base_offset and the cpu
type), which is the earliest point at which cs_native4k_prepare can work, and before every one of
those returns. The 0105 call site stays where it is; calling twice is harmless (the second time
sees csb_native4k_hashes already set and returns 0 from the first guard).

    python3 ci/patch-kernel-native4k-early.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-NATIVE4K-EARLY"

ANCHOR = """	tmp_blob.csb_cpu_type = cputype;
	tmp_blob.csb_cpu_subtype = cpusubtype & ~CPU_SUBTYPE_MASK;
	tmp_blob.csb_base_offset = base_offset;
"""

NEW = """	tmp_blob.csb_cpu_type = cputype;
	tmp_blob.csb_cpu_subtype = cpusubtype & ~CPU_SUBTYPE_MASK;
	tmp_blob.csb_base_offset = base_offset;

#if defined(XNU_NATIVE_CS_HASH_CACHE) && XNU_NATIVE_CS_HASH_CACHE
	/*
	 * HAKIOS-NATIVE4K-EARLY: the 0105 call site sits at the end of this function, past about
	 * twenty early returns (cs_blob_init_validated, reconstitute_code_signature,
	 * mac_vnode_check_signature, the slice matching, ...). The dyld shared cache is the blob that
	 * needs the conversion most, and it never got there: the probe in cs_native4k_prepare never
	 * printed. Convert here instead, right after the fields it needs are set.
	 */
	error = cs_native4k_prepare(vp, &tmp_blob);
	if (error != 0) {
		kprintf("hakios native4k: early prepare failed %d\\n", error);
		goto out;
	}
#endif
"""


def main(root):
    f = pathlib.Path(root) / "bsd/kern/ubc_subr.c"
    if not f.exists():
        print(f"patch-native4k-early: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-native4k-early: already applied")
        return 0
    if ANCHOR not in text:
        print("patch-native4k-early: MISS: the field assignment block is not where expected")
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-native4k-early: conversion moved ahead of the early returns in cs_blob_create")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-native4k-early.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
