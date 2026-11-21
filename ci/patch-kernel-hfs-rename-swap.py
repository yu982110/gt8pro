#!/usr/bin/env python3
"""Let HFS satisfy RENAME_SWAP, so Foundation's atomic writes and the swap file work.

Taken from the SM8850 port that boots iOS 16 from RAM (xnu/patches/0052-sweet-hfs-rename-swap.patch
there; 847 bytes).  Its note, and why it matches this log exactly:

    HFS has no RENAME_SWAP, which Foundation's atomic writes use
    (usermanagerd: "Failed to atomically swap writing file: Operation not supported")

Our own run (2026-11-21, ext-table boot) prints, right before the services come up:

    [   34.884957]: Failed to open swap file 2
    [   34.884958]: vm_swap_create_file failed @ 34 secs
    ...
    [   45.779072]: memorystatus_update: init: limit on pid 11 (-1MB F ) ...
    proctrace: pid 11 exec ok: usermanagerd
    ...
    panic(cpu 0): kernel data abort, pc in copyout, task pid 8 backboardd

vm_swap_create_file makes its file by rename; usermanagerd writes atomically by rename; HFS
answers ENOTSUP for both.  Swapping two regular files on one volume is what exchangedata
does, and HFS implements that (hfs_vnop_exchange), so fall back to it.

Difference from upstream: upstream guards this with ARM64_BOARD_CONFIG_SWEET (its board
macro).  This port builds the same lineage without that macro, so the fallback is applied
unconditionally -- it only ever runs where RENAME_SWAP would have returned ENOTSUP.

usage: patch-kernel-hfs-rename-swap.py <xnu source dir>
"""
import pathlib
import sys

MARK = "HAKIOS-HFS-RENAME-SWAP"

ANCHOR = """\t\t\tif (_err == ENOTSUP && flags == VFS_RENAME_SECLUDE) {
\t\t\t\t// Legacy...
\t\t\t\tif ((*fvpp)->v_mount->mnt_vtable->vfc_vfsflags & VFC_VFSVNOP_SECLUDE_RENAME) {
\t\t\t\t\tfcnp->cn_flags |= CN_SECLUDE_RENAME;
\t\t\t\t\t_err = VNOP_RENAME(fdvp, *fvpp, fcnp, tdvp, *tvpp, tcnp, ctx);
\t\t\t\t}
\t\t\t}
"""

ADD = """\t\t\t/*
\t\t\t * HAKIOS-HFS-RENAME-SWAP: HFS has no RENAME_SWAP, which Foundation's atomic
\t\t\t * writes use (and so does the swap file: "vm_swap_create_file failed"). Swapping
\t\t\t * two regular files on one volume is what exchangedata does, and HFS implements
\t\t\t * that, so fall back to it.  See ci/patch-kernel-hfs-rename-swap.py.
\t\t\t */
\t\t\tif (_err == ENOTSUP && flags == VFS_RENAME_SWAP && *tvpp != NULLVP &&
\t\t\t    vnode_isreg(*fvpp) && vnode_isreg(*tvpp) &&
\t\t\t    vnode_mount(*fvpp) == vnode_mount(*tvpp)) {
\t\t\t\t_err = VNOP_EXCHANGE(*fvpp, *tvpp, 0, ctx);
\t\t\t}
"""


def main(root):
    p = pathlib.Path(root) / "bsd/vfs/kpi_vfs.c"
    if not p.exists():
        print("patch-hfs-rename-swap: %s not found" % p)
        return 1
    t = p.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-hfs-rename-swap: already applied")
        return 0
    if t.count(ANCHOR) != 1:
        print("patch-hfs-rename-swap: MISS anchor (found %d)" % t.count(ANCHOR))
        return 1
    p.write_text(t.replace(ANCHOR, ANCHOR + ADD, 1), errors="surrogateescape")
    print("patch-hfs-rename-swap: RENAME_SWAP falls back to VNOP_EXCHANGE on HFS")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
