#!/usr/bin/env python3
"""patch-kernel-addfilesigs-probe.py -- catch the fcntl that registers the shared cache.

dyld cannot register the dyld shared cache. dyld/SharedCacheRuntime.cpp does this before hashing or
mapping anything:

    fsignatures_t siginfo;
    siginfo.fs_file_start = 0;
    siginfo.fs_blob_start = (void*)cache->header.codeSignatureOffset;
    siginfo.fs_blob_size  = (size_t)(cache->header.codeSignatureSize);
    int result = fcntl(fd, F_ADDFILESIGS_RETURN, &siginfo);
    if ( result == -1 ) {
            results->errorMessage = "code signature registration for shared cache failed";

so the kernel side of F_ADDFILESIGS_RETURN (bsd/kern/kern_descrip.c, case F_ADDFILESIGS*) is where
it dies. The previous version of this probe printed every call, and every call it caught was a
small one (16 KiB to 224 KiB, all succeeding) -- the shared cache's 6-7 MB registration never
appeared, in the ring on screen or in the persisted copy. So it fails before those prints: at

    fp->f_type != DTYPE_VNODE        -> EBADF
    vnode_getwithref(vp)             -> whatever that returns
    copyin(argp, &fs, sizeof_fs)     -> EFAULT

This version prints only when one of those three fails, so normal traffic is silent and anything
that appears is the answer. The entry/result prints stay, but only for blobs over 1 MiB, which is
where the shared cache lives -- small dylibs can no longer push it off the screen.

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

# the three ways this handler gives up before it prints anything useful
BADFTYPE_ANCHOR = """		if (fp->f_type != DTYPE_VNODE) {
			error = EBADF;
			goto out;
		}
		vp = (struct vnode *)fp_get_data(fp);
		proc_fdunlock(p);
"""

BADFTYPE_NEW = """		if (fp->f_type != DTYPE_VNODE) {
			/* HAKIOS-ADF-PROBE: the fd is not a vnode at all. */
			char probe_buf[96];

			snprintf(probe_buf, sizeof(probe_buf), "probe: adf BAD f_type %d cmd %d\\n",
			    fp->f_type, cmd);
			loader_log_line(probe_buf);
			error = EBADF;
			goto out;
		}
		vp = (struct vnode *)fp_get_data(fp);
		proc_fdunlock(p);
"""

GETREF_ANCHOR = """		error = vnode_getwithref(vp);
		if (error) {
			goto outdrop;
		}
"""

GETREF_NEW = """		error = vnode_getwithref(vp);
		if (error) {
			/* HAKIOS-ADF-PROBE: the vnode would not be referenced (reclaimed?). */
			char probe_buf[96];

			snprintf(probe_buf, sizeof(probe_buf), "probe: adf getref failed %d cmd %d\\n",
			    error, cmd);
			loader_log_line(probe_buf);
			goto outdrop;
		}
"""

COPYIN_ANCHOR = """		if (error) {
			vnode_put(vp);
			goto outdrop;
		}

		/*
		 * First check if we have something loaded a this offset
		 */
		blob = ubc_cs_blob_get(vp, CPU_TYPE_ANY, CPU_SUBTYPE_ANY, fs.fs_file_start);
"""

COPYIN_NEW = """		if (error) {
			/* HAKIOS-ADF-PROBE: copyin of the signature request failed. */
			char probe_buf[96];

			snprintf(probe_buf, sizeof(probe_buf), "probe: adf copyin failed %d cmd %d\\n",
			    error, cmd);
			loader_log_line(probe_buf);
			vnode_put(vp);
			goto outdrop;
		}

		if (fs.fs_blob_size > (1ULL << 20)) {
			/* HAKIOS-ADF-PROBE: a big registration -- this is the shared cache class of blob. */
			char probe_buf[192];

			snprintf(probe_buf, sizeof(probe_buf),
			    "probe: adf BIG cmd %d size %llu fstart %lld bstart %llu\\n",
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

			if (fs.fs_blob_size > (1ULL << 20)) {
				/* HAKIOS-ADF-PROBE: the big blob's fate. */
				char probe_buf[128];

				snprintf(probe_buf, sizeof(probe_buf),
				    "probe: adf BIG blob_add -> %d size %llu\\n",
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
        (BADFTYPE_ANCHOR, BADFTYPE_NEW, "bad f_type print"),
        (GETREF_ANCHOR, GETREF_NEW, "getref print"),
        (COPYIN_ANCHOR, COPYIN_NEW, "copyin + big-entry prints"),
        (ADD_ANCHOR, ADD_NEW, "big blob_add print"),
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
