#!/usr/bin/env python3
"""patch-kernel-ufsrw-all.py -- let ufsrw=1 cover writes outside the root image's extents.

sweet_ufs.c already has the whole write path (ufs_write_blocks, sweet_ufs_write, the DKIOC
write cases), but fx_write_allowed() only allows device blocks that lie inside the root image's
own extents (fx_ext). The HFS+ volume this port wants for /private/var lives on another
partition (oplusreserve2), so the mount comes back EROFS and init falls back to a memory
filesystem - and launchd's restore-datapartition then hangs on a read-only root.

With boot-arg ufsrw_all=1 the allowance is lifted to the whole device. It is a boot-arg, not a
constant, so the default stays exactly as before.

    python3 ci/patch-kernel-ufsrw-all.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-ANYWRITE"

ANCHOR = """	if (!fx_root || count == 0) {
		return false;
	}
"""

NEW = ANCHOR + """	/* HAKIOS-ANYWRITE: ufsrw_all=1 lets the whole device be written, not just the root
	 * image's extents: the HFS+ data volume for /private/var is on a partition of its own. */
	{
		uint32_t hakios_any = 0;

		if (PE_parse_boot_argn("ufsrw_all", &hakios_any, sizeof(hakios_any)) &&
		    hakios_any != 0) {
			return true;
		}
	}
"""


def main(root):
    f = pathlib.Path(root) / "bsd/dev/sweet_ufs.c"
    if not f.exists():
        print("patch-ufsrw-all: sweet_ufs.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-ufsrw-all: already applied")
        return 0
    if text.count(ANCHOR) != 1:
        print("patch-ufsrw-all: MISS: the fx_write_allowed preamble appears %d times"
              % text.count(ANCHOR))
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-ufsrw-all: ufsrw_all=1 makes the whole device writable")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-ufsrw-all.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
