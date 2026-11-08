#!/usr/bin/env python3
"""patch-kernel-ufsrw-bounds.py -- make ufsrw_all=1 lift the partition bounds too.

sweet_ufs.c's ufs_write_blocks() has two variants. With FUXI it checks fx_write_allowed() (the
extent allowlist, which patch-kernel-ufsrw-all.py widens); without FUXI it checks the write lies
inside the partition the driver was initialised for:

    if (!su.writable || !su.nblocks || count == 0 || count > BOUNCE_SIZE / UFS_BLOCK ||
        lba < su.first_block || lba - su.first_block >= su.nblocks ||
        count > su.nblocks - (lba - su.first_block) || lba + count - 1 > UINT32_MAX) {

The port boots without FUXI, so the HFS+ volume on oplusreserve2 (a partition of its own) fails
that test and mount() comes back EROFS - which is why /private/var falls back to a memory
filesystem and launchd's restore-datapartition never has a data volume.

With boot-arg ufsrw_all=1 the partition bounds are skipped (the bounce size and the 32-bit LBA
limit still hold). Same boot-arg as patch-kernel-ufsrw-all.py; both are needed.

    python3 ci/patch-kernel-ufsrw-bounds.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-ANYWRITE-BOUNDS"

ANCHOR = """	uint64_t
	ufs_write_blocks(uint64_t lba, uint32_t count)
"""

HELPER = """/*
 * HAKIOS-ANYWRITE-BOUNDS: ufsrw_all=1 allows writes outside the partition the driver was
 * initialised for (the HFS+ volume for /private/var is on oplusreserve2).
 */
static bool
hakios_anywrite(void)
{
	uint32_t any = 0;

	return PE_parse_boot_argn("ufsrw_all", &any, sizeof(any)) && any != 0;
}

	uint64_t
	ufs_write_blocks(uint64_t lba, uint32_t count)
"""

COND_OLD = """	if (!su.writable || !su.nblocks || count == 0 || count > BOUNCE_SIZE / UFS_BLOCK ||
	    lba < su.first_block || lba - su.first_block >= su.nblocks ||
	    count > su.nblocks - (lba - su.first_block) || lba + count - 1 > UINT32_MAX) {
"""

COND_NEW = """	if (!su.writable ||
	    (!hakios_anywrite() && (!su.nblocks || count == 0 ||
	     count > BOUNCE_SIZE / UFS_BLOCK ||
	     lba < su.first_block || lba - su.first_block >= su.nblocks ||
	     count > su.nblocks - (lba - su.first_block))) ||
	    (hakios_anywrite() && (count == 0 || count > BOUNCE_SIZE / UFS_BLOCK)) ||
	    lba + count - 1 > UINT32_MAX) {
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
    if text.count(ANCHOR) != 1:
        print("patch-ufsrw-bounds: MISS: ufs_write_blocks header appears %d times"
              % text.count(ANCHOR))
        return 1
    if text.count(COND_OLD) != 1:
        print("patch-ufsrw-bounds: MISS: the bounds condition appears %d times"
              % text.count(COND_OLD))
        return 1
    text = text.replace(ANCHOR, HELPER, 1)
    text = text.replace(COND_OLD, COND_NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-ufsrw-bounds: ufsrw_all=1 skips the partition bounds in ufs_write_blocks")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-ufsrw-bounds.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
