#!/usr/bin/env python3
"""patch-kernel-ufsrw-bounds.py -- ufsrw_all=1 lifts the partition bounds in ufs_write_blocks.

sweet_ufs.c's ufs_write_blocks() has two variants. With FUXI it checks fx_write_allowed(); without
FUXI it checks the write lies inside the partition the driver was initialised for:

	if (!su.writable || !su.nblocks || count == 0 || count > BOUNCE_SIZE / UFS_BLOCK ||
	    lba < su.first_block || lba - su.first_block >= su.nblocks ||
	    count > su.nblocks - (lba - su.first_block) || lba + count - 1 > UINT32_MAX) {

The port boots without FUXI and the HFS+ data volume lives on oplusreserve2, a partition of its
own, so every write the HFS driver makes while mounting read-write (journal init first) is
refused. With boot-arg ufsrw_all=1 the partition bounds are skipped; the bounce size and the
32-bit LBA limit still hold, so nothing outside one transfer can run away.

    python3 ci/patch-kernel-ufsrw-bounds.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-ANYWRITE-BOUNDS"

FUNC_ANCHOR = """ufs_write_blocks(uint64_t lba, uint32_t count)
{
"""

FUNC_NEW = FUNC_ANCHOR + """	bool hakios_any = false;
	{
		uint32_t hakios_any_arg = 0;

		/* HAKIOS-ANYWRITE-BOUNDS: ufsrw_all=1 skips the partition bounds below. */
		hakios_any = PE_parse_boot_argn("ufsrw_all", &hakios_any_arg,
		    sizeof(hakios_any_arg)) && hakios_any_arg != 0;
	}
"""

COND_ANCHOR = """	if (!su.writable || !su.nblocks || count == 0 || count > BOUNCE_SIZE / UFS_BLOCK ||
	    lba < su.first_block || lba - su.first_block >= su.nblocks ||
	    count > su.nblocks - (lba - su.first_block) || lba + count - 1 > UINT32_MAX) {
"""

COND_NEW = """	if (!su.writable || lba + count - 1 > UINT32_MAX ||
	    (hakios_any && (count == 0 || count > BOUNCE_SIZE / UFS_BLOCK)) ||
	    (!hakios_any && (!su.nblocks || count == 0 || count > BOUNCE_SIZE / UFS_BLOCK ||
	     lba < su.first_block || lba - su.first_block >= su.nblocks ||
	     count > su.nblocks - (lba - su.first_block)))) {
"""


def main(root):
    f = pathlib.Path(root) / "bsd/dev/sweet_ufs.c"
    if not f.exists():
        print("patch-ufsrw-bounds: sweet_ufs.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-ufsrw-bounds: already applied")
        return 0
    if text.count(FUNC_ANCHOR) != 1:
        print("patch-ufsrw-bounds: MISS: ufs_write_blocks header appears %d times"
              % text.count(FUNC_ANCHOR))
        return 1
    if text.count(COND_ANCHOR) != 1:
        print("patch-ufsrw-bounds: MISS: the bounds condition appears %d times"
              % text.count(COND_ANCHOR))
        return 1
    text = text.replace(FUNC_ANCHOR, FUNC_NEW, 1)
    text = text.replace(COND_ANCHOR, COND_NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-ufsrw-bounds: ufsrw_all=1 skips the partition bounds in ufs_write_blocks")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-ufsrw-bounds.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
