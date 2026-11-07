#!/usr/bin/env python3
"""patch-kernel-addfilesigs-probe.py -- watch the fcntl that registers the shared cache signature.

dyld cannot register the dyld shared cache. The failing call is not the page hashing and not the
mapping -- dyld/SharedCacheRuntime.cpp does this before either:

    fsignatures_t siginfo;
    siginfo.fs_file_start = 0;
    siginfo.fs_blob_start = (void*)cache->header.codeSignatureOffset;
    siginfo.fs_blob_size  = (size_t)(cache->header.codeSignatureSize);
    int result = fcntl(fd, F_ADDFILESIGS_RETURN, &siginfo);
    if ( result == -1 ) {
            results->errorMessage = "code signature registration for shared cache failed";

so the kernel side of F_ADDFILESIGS_RETURN (bsd/kern/kern_descrip.c, case F_ADDFILESIGS*) is
where it dies. That handler has these exits before it ever reaches cs_blob_create:

    vnode_getwithref        -> EBADF-ish
    copyin                  -> EFAULT
    fs_blob_size > 40 MB    -> E2BIG
    ubc_cs_blob_allocate    -> ENOMEM
    vn_rdwr                 -> EIO
    ubc_cs_blob_add         -> whatever cs_blob_create decides

This prints the command and the three signature fields on entry, and the result of
ubc_cs_blob_add, through loader_log_line so the lines land on the loader's screen. No "probe: adf"
lines at all would mean dyld never made the call -- a cache-path problem in userspace rather than
a kernel one.

    python3 ci/patch-kernel-addfilesigs-probe.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-ADF-PROBE"

DECL_ANCHORS = (
    "#include <sys/filedesc.h>\n",
    "#include <sys/fcntl.h>\n",
    "#include <sys/syscall.h>\n",
)

DECL = """/* HAKIOS-ADF-PROBE: loader_log_line writes into the loader's ring, which is on screen. */
#include <libkern/libkern.h>
extern void loader_log_line(const char *line);
"""

# right after the copyin, before the "First check if we have something loaded" comment
ENTRY_ANCHOR = """		if (error) {
			vnode_put(vp);
			goto outdrop;
		}

		/*
		 * First check if we have something loaded a this offset
		 */
		blob = ubc_cs_blob_get(vp, CPU_TYPE_ANY, CPU_SUBTYPE_ANY, fs.fs_file_start);
"""

ENTRY_NEW = """		if (error) {
			vnode_put(vp);
			goto outdrop;
		}

		{
			/* HAKIOS-ADF-PROBE: the syscall arrived, with these signature fields. */
			char probe_buf[192];

			snprintf(probe_buf, sizeof(probe_buf),
			    "probe: adf cmd %d size %llu fstart %lld bstart %llu\\n",
			    cmd, (unsigned long long)fs.fs_blob_size,
			    (long long)fs.fs_file_start, (unsigned long long)fs.fs_blob_start);
			loader_log_line(probe_buf);
		}

		/*
		 * First check if we have something loaded a this offset
		 */
		blob = ubc_cs_blob_get(vp, CPU_TYPE_ANY, CPU_SUBTYPE_ANY, fs.fs_file_start);
"""

ADD_ANCHOR = """			    blob_add_flags,
			    &blob);

			/* ubc_blob_add() has consumed "kernel_blob_addr" if it is zeroed */
			if (error) {
"""

ADD_NEW = """			    blob_add_flags,
			    &blob);

			{
				/* HAKIOS-ADF-PROBE: what ubc_cs_blob_add (and cs_blob_create) decided. */
				char probe_buf[128];

				snprintf(probe_buf, sizeof(probe_buf),
				    "probe: adf blob_add -> %d size %llu\\n",
				    error, (unsigned long long)fs.fs_blob_size);
				loader_log_line(probe_buf);
			}

			/* ubc_blob_add() has consumed "kernel_blob_addr" if it is zeroed */
			if (error) {
"""


def main(root):
    f = pathlib.Path(root) / "bsd/kern/kern_descrip.c"
    if not f.exists():
        print(f"patch-adf-probe: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-adf-probe: already applied")
        return 0

    bad = 0
    for anchor in DECL_ANCHORS:
        if anchor in text:
            text = text.replace(anchor, anchor + DECL, 1)
            print("patch-adf-probe: declaration")
            break
    else:
        print("patch-adf-probe: MISS: declaration (no include anchor)")
        bad = 1

    for anchor, new, what in (
        (ENTRY_ANCHOR, ENTRY_NEW, "entry print"),
        (ADD_ANCHOR, ADD_NEW, "blob_add print"),
    ):
        if anchor in text:
            text = text.replace(anchor, new, 1)
            print(f"patch-adf-probe: {what}")
        else:
            print(f"patch-adf-probe: MISS: {what} (anchor not found)")
            bad = 1

    f.write_text(text, errors="surrogateescape")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-addfilesigs-probe.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
