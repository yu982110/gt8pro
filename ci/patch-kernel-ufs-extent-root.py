#!/usr/bin/env python3
"""HAKIOS-UFS-EXTENT-ROOT: read the flash root image through /chosen's extent table.

The 3.4 GB APFS root image is one file inside Android's userdata: it does not necessarily
start where the loader's synthetic "rootapfs" window starts, and it may be cut into pieces.
The loader already publishes what the file really is, in /chosen (xnuloader/loader.c,
dt_root_extents()):

    fuxi-root-extents = u64 triples {file block, userdata block, blocks} (4 KiB units)
    fuxi-root-blocks  = the image's size in 4 KiB blocks

The kernel driver that is actually built -- hakios/drivers/storage/hakios.ufs.c, copied to
bsd/dev/hakios.ufs.c by xnu/apply.sh and xnu/sync-drivers.sh -- has no use for it today: its
/dev/ufs_rootapfs is the loader's contiguous 830000-block window of userdata, read as-is.
That is the image only while the file happens to sit at the window's first block; a moved or
fragmented image is read as whatever else is there (and APFS then fails its checks as EIO).

This patch makes the loader's rootapfs partition a window translated, block by block, through
the extent table: every read of /dev/ufs_rootapfs goes to the userdata blocks the image really
occupies -- by this kernel's own UFS driver when ufs_native= is in, by the loader's block
service otherwise -- and the device is exactly fuxi-root-blocks long. The other devices, the
write path and the native list switching are untouched. ufs_extent_root=0 in the boot
arguments turns the mapping off (the old raw-window behaviour) for A/B runs.

Idempotent. Nothing is written unless every place the patch goes is found: a tree without the
driver, or with a driver this patch does not know, prints MISS and exits 1.

    python3 ci/patch-kernel-ufs-extent-root.py <xnu tree>
    python3 ci/patch-kernel-ufs-extent-root.py hakios/xnu-src/xnu-8796.121.2

Expected lines in the kernel log (they reach the on-device ring through
patch-kernel-ufs-diag.py's printf redirection, when that step is also in the workflow):

    ufs: extent root: the image is 1 extent(s), 830000 blocks (of 4 KB) in userdata; /dev/ufs_rootapfs reads it
    ufs: extent root: no fuxi-root-extents in /chosen yet; /dev/ufs_rootapfs reads its raw window
    ufs: extent root: extent 0 of /chosen is not one userdata has (file 0, block 78643200, 830000 blocks)
    ufs: extent root: off (ufs_extent_root=0)

The reads themselves keep their own lines ("ufs: native: ...", "ufs: <name>: blocks ...").
"""
import pathlib
import sys

MARK = "HAKIOS-UFS-EXTENT-ROOT"

# ------------------------------------------------------------------------------------------------
# The exact places of the driver this patch goes, and what it puts there. Every "old" must occur
# exactly once: if the driver has drifted, the patch says which place it did not find and writes
# nothing.

INCLUDES_OLD = '#include "hakios.ufshc.h"\n'
INCLUDES_NEW = INCLUDES_OLD + (
    r"""#include <pexpert/device_tree.h>        /* HAKIOS-UFS-EXTENT-ROOT: /chosen's root extents */
"""
)

GLOBALS_OLD = "static uint64_t ufs_native_fallbacks, ufs_mailbox_refused;\n"
GLOBALS_NEW = GLOBALS_OLD + (
    r"""
/*
 * HAKIOS-UFS-EXTENT-ROOT: the root image is a file inside userdata, cut into 4 KiB extents that
 * the loader publishes in /chosen (fuxi-root-extents: u64 triples {file block, userdata block,
 * blocks}; fuxi-root-blocks: the file size). The loader's rootapfs partition is only a
 * contiguous window of userdata, which is the image only while the file sits at its first
 * block; these translate every read of it to the blocks the image really occupies and make the
 * device exactly as long as the image. Read by this kernel's own UFS driver when it is in, by
 * the loader's block service otherwise -- both take a block of userdata the same way.
 */
#define UFS_ROOT_MAX_EXTENTS 1024
struct ufs_root_extent {
	uint64_t file, part, count;             /* 4 KiB blocks: image block, userdata block, run */
};
static struct ufs_root_extent ufs_root_ext[UFS_ROOT_MAX_EXTENTS];
static uint32_t ufs_root_next;                  /* extents read and checked */
static uint32_t ufs_root_part = BLOCK_MAX_PARTS;/* the loader's rootapfs entry */
static uint32_t ufs_root_host = BLOCK_MAX_PARTS;/* userdata, the partition the file is in */
static uint64_t ufs_root_blocks;                /* image size in 4 KiB blocks */
static bool ufs_root_on;                        /* the table is loaded and checked */
static int ufs_root_read(uint64_t offset, uint8_t *dst, uint64_t len);
"""
)

PART_BYTES_OLD = (
    "static uint64_t\n"
    "ufs_part_bytes(uint32_t part)\n"
    "{\n"
    "\treturn ufs_page->part[part].blocks * ufs_page->part[part].block_size;\n"
    "}\n"
)
PART_BYTES_NEW = (
    "static uint64_t\n"
    "ufs_part_bytes(uint32_t part)\n"
    "{\n"
    "\tuint64_t blocks = ufs_page->part[part].blocks;\n"
    "\n"
    "\t/* HAKIOS-UFS-EXTENT-ROOT: the root device is the image, not the window drawn around it */\n"
    "\tif (ufs_root_on && part == ufs_root_part && ufs_root_blocks < blocks) {\n"
    "\t\tblocks = ufs_root_blocks;\n"
    "\t}\n"
    "\treturn blocks * ufs_page->part[part].block_size;\n"
    "}\n"
)

DISPATCH_OLD = (
    "static int\n"
    "ufs_read(uint32_t part, uint64_t offset, uint8_t *dst, uint64_t len)\n"
    "{\n"
)
DISPATCH_NEW = DISPATCH_OLD + (
    "\t/* HAKIOS-UFS-EXTENT-ROOT: the root image is read through its extents, not as userdata */\n"
    "\tif (ufs_root_on && part == ufs_root_part) {\n"
    "\t\treturn ufs_root_read(offset, dst, len);\n"
    "\t}\n"
)

FUNCS_OLD = "\treturn ufs_read_mailbox(part, offset, dst, len);\n}\n\n"
FUNCS_NEW = FUNCS_OLD + (
    r"""/*
 * HAKIOS-UFS-EXTENT-ROOT: the userdata block that holds 4 KiB block lblk of the root image,
 * and the blocks left in that extent. The extents are sorted by file block (ufs_root_load()
 * checks that they tile the image): a binary search, then the offset inside the one found.
 */
static bool
ufs_root_map(uint64_t lblk, uint64_t *part, uint64_t *run)
{
	uint32_t lo = 0, hi = ufs_root_next;

	while (lo < hi) {
		uint32_t mid = lo + (hi - lo) / 2;

		if (ufs_root_ext[mid].file <= lblk) {
			lo = mid + 1;
		} else {
			hi = mid;
		}
	}
	if (lo == 0 || lblk - ufs_root_ext[lo - 1].file >= ufs_root_ext[lo - 1].count) {
		return false;
	}
	*part = ufs_root_ext[lo - 1].part + (lblk - ufs_root_ext[lo - 1].file);
	*run = ufs_root_ext[lo - 1].count - (lblk - ufs_root_ext[lo - 1].file);
	return true;
}

/*
 * Bytes [offset, offset + len) of the root image, read from the userdata blocks the extents
 * name: ufs_read() of the host partition, so the native driver is tried first and the loader's
 * block service covers the rest, exactly as for any other partition.
 */
static int
ufs_root_read(uint64_t offset, uint8_t *dst, uint64_t len)
{
	int error = 0;

	while (len > 0 && error == 0) {
		uint64_t lblk = offset / 4096, skip = offset % 4096, part, run, blocks, take;

		if (ufs_root_blocks == 0 || lblk >= ufs_root_blocks) {
			return EINVAL;
		}
		if (!ufs_root_map(lblk, &part, &run)) {
			return EIO;
		}
		blocks = (skip + len + 4095) / 4096;    /* the blocks this piece covers */
		if (blocks > run) {
			blocks = run;                   /* an extent ends here: one piece at a time */
		}
		take = blocks * 4096 - skip;
		if (take > len) {
			take = len;
		}
		error = ufs_read(ufs_root_host, part * 4096 + skip, dst, take);
		offset += take;
		dst += take;
		len -= take;
	}
	return error;
}

/*
 * HAKIOS-UFS-EXTENT-ROOT: read the loader's /chosen and check its extent table against the
 * userdata partition the loader published. Called once, after the partition table exists (in
 * hakios_ufs_init); ufs_extent_root=0 leaves the loader's raw window as it was. A table that
 * is wrong is not used: the raw window is what there is then (and says so).
 */
static int
ufs_root_load(void)
{
	DTEntry chosen;
	uint64_t const *ext = NULL, *want = NULL;
	unsigned int size = 0, wsize = 0, n;
	uint64_t pos = 0, blocks;
	uint32_t host = BLOCK_MAX_PARTS, root = BLOCK_MAX_PARTS, enable = 1;

	if (PE_parse_boot_argn("ufs_extent_root", &enable, sizeof(enable)) && enable == 0) {
		printf("ufs: extent root: off (ufs_extent_root=0)\n");
		return ENOENT;
	}
	for (uint32_t i = 0; i < ufs_parts; i++) {
		const char *name = (const char *)ufs_page->part[i].name;

		if (strcmp(name, "rootapfs") == 0) {
			root = i;
		} else if (strcmp(name, "userdata") == 0) {
			host = i;
		}
	}
	if (root >= BLOCK_MAX_PARTS || host >= BLOCK_MAX_PARTS || ufs_root_next != 0) {
		/* (no rootapfs window, no userdata, or already loaded: nothing to map it through) */
		return ENOENT;
	}
	if (ufs_page->part[root].block_size != 4096 || ufs_page->part[host].block_size != 4096 ||
	    (ufs_page->part[root].flags & BLOCK_PART_UNIT) ||
	    (ufs_page->part[host].flags & BLOCK_PART_UNIT)) {
		printf("ufs: extent root: rootapfs/userdata are not 4096-byte partitions: not mapped\n");
		return EINVAL;
	}
	if (SecureDTLookupEntry(NULL, "/chosen", &chosen) != kSuccess ||
	    SecureDTGetProperty(chosen, "fuxi-root-extents", (void const **)&ext, &size) != kSuccess ||
	    SecureDTGetProperty(chosen, "fuxi-root-blocks", (void const **)&want, &wsize) != kSuccess) {
		printf("ufs: extent root: no fuxi-root-extents in /chosen yet; /dev/ufs_%s reads its raw window\n",
		    (const char *)ufs_page->part[root].name);
		return ENOENT;
	}
	if (ext == NULL || want == NULL || wsize != sizeof(uint64_t) || size == 0 ||
	    size % (3 * sizeof(uint64_t)) != 0 ||
	    size / (3 * sizeof(uint64_t)) > UFS_ROOT_MAX_EXTENTS) {
		printf("ufs: extent root: /chosen's fuxi-root-extents is not a table this kernel reads\n");
		return EINVAL;
	}
	n = size / (3 * sizeof(uint64_t));
	blocks = *want;
	for (unsigned int i = 0; i < n && pos < blocks; i++) {
		uint64_t file = ext[3 * i], part = ext[3 * i + 1], count = ext[3 * i + 2];

		if (file != pos || count == 0 || part > ufs_page->part[host].blocks ||
		    count > ufs_page->part[host].blocks - part) {
			printf("ufs: extent root: extent %u of /chosen is not one userdata has (file %llu, "
			    "block %llu, %llu blocks)\n", i, file, part, count);
			return EINVAL;
		}
		if (count > blocks - pos) {
			count = blocks - pos;           /* (the file may be described longer than the image) */
		}
		ufs_root_ext[i].file = file;
		ufs_root_ext[i].part = part;
		ufs_root_ext[i].count = count;
		pos += count;
	}
	if (n == 0 || pos != blocks) {
		printf("ufs: extent root: /chosen's extents cover %llu of %llu blocks: not mapped\n", pos,
		    blocks);
		return EINVAL;
	}
	ufs_root_next = n;
	ufs_root_part = root;
	ufs_root_host = host;
	ufs_root_blocks = blocks;
	ufs_root_on = true;
	printf("ufs: extent root: the image is %u extent(s), %llu blocks (of 4 KB) in %s; "
	    "/dev/ufs_%s reads it\n", n, blocks, (const char *)ufs_page->part[host].name,
	    (const char *)ufs_page->part[root].name);
	return 0;
}

"""
)

INIT_OLD = (
    "\t    \"through the loader's page at %llx and its %u KB buffer\\n\", made, pa, "
    "ufs_buffer_size >> 10);\n"
)
INIT_NEW = INIT_OLD + (
    "\n"
    "\t/* HAKIOS-UFS-EXTENT-ROOT: /dev/ufs_rootapfs through the extent table in /chosen, if there */\n"
    "\t(void)ufs_root_load();\n"
)

REPLACEMENTS = [
    ("the hakios.ufshc.h include", INCLUDES_OLD, INCLUDES_NEW),
    ("the native globals", GLOBALS_OLD, GLOBALS_NEW),
    ("ufs_part_bytes()", PART_BYTES_OLD, PART_BYTES_NEW),
    ("ufs_read()'s entry", DISPATCH_OLD, DISPATCH_NEW),
    ("the end of ufs_read()", FUNCS_OLD, FUNCS_NEW),
    ("the partition-report printf in hakios_ufs_init()", INIT_OLD, INIT_NEW),
]


def find_driver(argument: str):
    """The driver the kernel is built from, found from the xnu tree (or a repository root)."""
    root = pathlib.Path(argument)
    candidates = [
        # <repo>/xnu-src/xnu-... -> three parents up is the directory the repository is in, as
        # the other driver patch scripts locate it.
        root.parent.parent.parent / "drivers/storage/hakios.ufs.c",
        # <repo>/xnu-src/xnu-... -> two parents up is the repository root.
        root.parent.parent / "drivers/storage/hakios.ufs.c",
        # the argument is the repository itself
        root / "drivers/storage/hakios.ufs.c",
        # the xnu tree after xnu/apply.sh has copied the drivers in
        root / "bsd/dev/hakios.ufs.c",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    # anything named that, under the tree that was given (the copy an earlier apply.sh made)
    return next((p for p in root.rglob("hakios.ufs.c")), None)


def patch_driver(path: pathlib.Path) -> int:
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    if MARK in text:
        print("patch-ufs-extent-root: %s already has %s" % (path, MARK))
        return 0
    for what, old, new in REPLACEMENTS:
        found = text.count(old)
        if found != 1:
            print("patch-ufs-extent-root: MISS %s in %s (%d match(es) of the text this patch knows)"
                  % (what, path, found))
            return 1
        text = text.replace(old, new, 1)
    if text.count(MARK) < 1:
        print("patch-ufs-extent-root: MISS the patch did not write its mark into %s" % path)
        return 1
    path.write_text(text, encoding="utf-8", errors="surrogateescape")
    print("patch-ufs-extent-root: patched %s" % path)
    print("patch-ufs-extent-root: %s added: /dev/ufs_rootapfs reads /chosen's extents "
          "(ufs_extent_root=0: the loader's raw window)" % MARK)
    return 0


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else "xnu"
    driver = find_driver(root)
    if driver is None:
        print("patch-ufs-extent-root: MISS hakios/drivers/storage/hakios.ufs.c (searched from %s)" % root)
        return 1
    return patch_driver(driver)


if __name__ == "__main__":
    sys.exit(main())
