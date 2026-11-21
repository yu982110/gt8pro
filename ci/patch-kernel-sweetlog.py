#!/usr/bin/env python3
"""Keep the kernel console on the disk, so it can be read from Android afterwards.

M1 of docs/MINIMAL-CHANGESET.md, taken from the SM8850 port that boots iOS 16 from RAM
(its xnu/patches/0047-sweet-persistent-console-log.patch).  Every character that reaches
the console -- kprintf and /dev/console writes alike -- passes _cnputs(); it is copied into
a 4 MiB ring, and a kernel thread writes new ring blocks to the last 4 MiB + 4 KiB of the
UFS partition once a second.  Block 0 of that area is a header:

    "SWEETLOG" | u32 version 1 | u32 ring size | u64 total bytes | u64 flush sequence

so byte n of the console sits at ring offset n % ring size.  Reading it needs no loader, no
panic and no fastboot session: reboot to Android and dd the tail of the partition.

Layout differs from the original only in where the ring lives: in serial_console.c (which is
always linked, because _cnputs is there), not in sweet_ufs.c.  The first attempt put both in
sweet_ufs.c and the link failed with "_sweet_log_append referenced from _cnputs" -- the UFS
file is compiled but its object did not reach the link.  Now the always-linked side owns the
ring and the UFS side only reads it, so this cannot fail to link.

Boundary conditions: ufs_write_blocks(u64,u32) / ufs_sync_cache() / sweet_ufs_lock / su
(first_block, nblocks, writable, dma) come from patches 0028 and 0030, all inside
`#if defined(ARM64_BOARD_CONFIG_SWEET)`; the writer is inserted right before
sweet_ufs_init(), inside that guard.  Start needs su.writable (boot-arg ufsrw=1) and a
partition of at least 8 MiB + 4 KiB beyond the root image.  Off with boot-arg sweetlog=0.

usage: patch-kernel-sweetlog.py <xnu source dir>
"""
import pathlib
import sys

MARK = "HAKIOS-SWEETLOG"

# --------------------------------------------------- osfmk/console/serial_console.c
# The ring lives here: this file is always linked, so nothing it defines can go missing.
CON_OLD = """static inline void
_cnputs(char *c, int size, bool poll)
{
\textern int disableConsoleOutput;
"""
CON_NEW = """/*
 * HAKIOS-SWEETLOG: the console's copy of itself.  Every character printed -- kprintf and
 * /dev/console writes alike -- comes through _cnputs(); it is also kept in this ring, which
 * bsd/dev/sweet_ufs.c writes to the last 4 MiB + 4 KiB of the UFS partition once a second.
 * See ci/patch-kernel-sweetlog.py for the header and how to read it back from Android.
 */
#define SWEET_LOG_RING_SIZE     (4u << 20)

static char sweet_log_ring[SWEET_LOG_RING_SIZE];
static uint64_t _Atomic sweet_log_total;

void
sweet_log_append(const char *c, int size)
{
\tif (size <= 0) {
\t\treturn;
\t}
\tuint64_t at = os_atomic_add_orig(&sweet_log_total, (uint64_t)size, relaxed);
\tfor (int i = 0; i < size; i++) {
\t\tsweet_log_ring[(at + (uint64_t)i) % SWEET_LOG_RING_SIZE] = c[i];
\t}
}

/* For bsd/dev/sweet_ufs.c's writer: the ring and how much has ever been written. */
char *
sweet_log_ring_base(void)
{
\treturn sweet_log_ring;
}

uint64_t
sweet_log_written(void)
{
\treturn os_atomic_load(&sweet_log_total, relaxed);
}

static inline void
_cnputs(char *c, int size, bool poll)
{
\textern int disableConsoleOutput;

\tsweet_log_append(c, size);
"""

# ------------------------------------------------------------------ bsd/dev/sweet_ufs.c
UFS_INC_OLD = "extern ppnum_t pmap_find_phys(pmap_t pmap, addr64_t va);\n"
UFS_INC_NEW = """extern ppnum_t pmap_find_phys(pmap_t pmap, addr64_t va);

/* HAKIOS-SWEETLOG: the ring lives in osfmk/console/serial_console.c; this file writes it out.
 * See ci/patch-kernel-sweetlog.py. */
extern kern_return_t kernel_thread_start(thread_continue_t, void *, thread_t *);
extern void thread_deallocate(thread_t);
extern int hz;
extern char *sweet_log_ring_base(void);
extern uint64_t sweet_log_written(void);
/* 0030 defines these static, after this point in the file: forward-declare them the same way. */
static int ufs_write_blocks(uint64_t lba, uint32_t count);
static int ufs_sync_cache(void);
static void sweet_log_start(void);
"""

UFS_READY_OLD = "\tsu.ready = true;\n"
UFS_READY_NEW = "\tsu.ready = true;\n\tsweet_log_start();\n"

# inside the guard, immediately before the init function
UFS_ANCHOR_OLD = "static int\nsweet_ufs_init(void)\n"
UFS_ANCHOR_NEW = """/*
 * HAKIOS-SWEETLOG: persistent console log.  The ring is in serial_console.c; a thread copies
 * new blocks of it to the last 4 MiB + 4 KiB of the partition about once a second -- far
 * behind the root image at its start.  Device block 0 of that area is the header described
 * in ci/patch-kernel-sweetlog.py.
 */
#define SWEET_LOG_SIZE          (4u << 20)
#define SWEET_LOG_BLOCKS        (1 + SWEET_LOG_SIZE / UFS_BLOCK)

static int sweet_log_chan;

struct sweet_log_header {
\tchar magic[8];
\tuint32_t version, size;
\tuint64_t total, seq;
};

static void
sweet_log_thread(__unused void *arg, __unused wait_result_t wr)
{
\tchar *ring = sweet_log_ring_base();
\tuint64_t base = su.first_block + su.nblocks - SWEET_LOG_BLOCKS;
\tuint64_t flushed = 0, seq = 0;
\tvoid *bounce = (void *)(su.dma + DMA_BOUNCE);

\tprintf("SWEET LOG: console log to the last %u KiB of the partition (block 0x%llx)\\n",
\t    SWEET_LOG_BLOCKS * UFS_BLOCK >> 10, base);
\tfor (;;) {
\t\t(void)tsleep(&sweet_log_chan, PZERO, "sweetlog", hz);
\t\tuint64_t total = sweet_log_written();
\t\tif (total == flushed) {
\t\t\tcontinue;
\t\t}
\t\tuint64_t pos = total - flushed >= SWEET_LOG_SIZE ? total - SWEET_LOG_SIZE : flushed;
\t\tint error = 0;

\t\tlck_mtx_lock(&sweet_ufs_lock);
\t\twhile (pos < total && !error) {
\t\t\tuint32_t off = (uint32_t)(pos % SWEET_LOG_SIZE);
\t\t\tuint32_t blk = off / UFS_BLOCK;
\t\t\tuint64_t need = ((uint64_t)off % UFS_BLOCK + (total - pos) + UFS_BLOCK - 1) / UFS_BLOCK;
\t\t\tuint32_t n = (uint32_t)MIN(MIN(need, (uint64_t)(SWEET_LOG_SIZE / UFS_BLOCK - blk)),
\t\t\t    (uint64_t)(BOUNCE_SIZE / UFS_BLOCK));
\t\t\tmemcpy(bounce, ring + (uint64_t)blk * UFS_BLOCK, n * UFS_BLOCK);
\t\t\terror = ufs_write_blocks(base + 1 + blk, n);
\t\t\tpos += (uint64_t)(blk + n) * UFS_BLOCK - off;
\t\t}
\t\tif (!error) {
\t\t\tstruct sweet_log_header h = {
\t\t\t\t.magic = { 'S', 'W', 'E', 'E', 'T', 'L', 'O', 'G' },
\t\t\t\t.version = 1, .size = SWEET_LOG_SIZE, .total = total, .seq = ++seq,
\t\t\t};
\t\t\tbzero(bounce, UFS_BLOCK);
\t\t\tmemcpy(bounce, &h, sizeof(h));
\t\t\terror = ufs_write_blocks(base, 1);
\t\t}
\t\tif (!error) {
\t\t\terror = ufs_sync_cache();
\t\t}
\t\tlck_mtx_unlock(&sweet_ufs_lock);
\t\tif (error) {
\t\t\tprintf("SWEET LOG: write failed (%d), stopping\\n", error);
\t\t\treturn;
\t\t}
\t\tflushed = total;
\t}
}

static void
sweet_log_start(void)
{
\tuint32_t on = 1;
\tthread_t thread;

\tPE_parse_boot_argn("sweetlog", &on, sizeof(on));
\tif (!on || !su.writable || su.nblocks < 2 * SWEET_LOG_BLOCKS) {
\t\tprintf("SWEET LOG: off (on=%u writable=%d blocks=%llu)\\n", on, su.writable, su.nblocks);
\t\treturn;
\t}
\tif (kernel_thread_start(sweet_log_thread, NULL, &thread) == KERN_SUCCESS) {
\t\tthread_deallocate(thread);
\t}
}

static int
sweet_ufs_init(void)
"""


def main(root):
    rc = 0
    ufs = pathlib.Path(root) / "bsd/dev/sweet_ufs.c"
    con = pathlib.Path(root) / "osfmk/console/serial_console.c"
    for f in (ufs, con):
        if not f.exists():
            print("patch-sweetlog: %s not found" % f)
            return 1
    ut = ufs.read_text(errors="surrogateescape")
    ct = con.read_text(errors="surrogateescape")
    if "SWEET_LOG_SIZE" in ut and "sweet_log_append(c, size);" in ct:
        print("patch-sweetlog: already there")
        return 0

    # serial console first: the ring has to exist for the UFS side to refer to
    if "sweet_log_append(c, size);" not in ct:
        if CON_OLD not in ct:
            print("patch-sweetlog: MISS anchor: _cnputs")
            rc = 1
        else:
            ct = ct.replace(CON_OLD, CON_NEW, 1)
            print("patch-sweetlog: serial_console.c owns the ring")
            con.write_text(ct, errors="surrogateescape")

    for old, new, name in ((UFS_INC_OLD, UFS_INC_NEW, "sweet_ufs.c declarations"),
                           (UFS_READY_OLD, UFS_READY_NEW, "sweet_ufs.c start call"),
                           (UFS_ANCHOR_OLD, UFS_ANCHOR_NEW, "sweet_ufs.c writer (inside the guard)")):
        if old not in ut:
            print("patch-sweetlog: MISS anchor: %s" % name)
            rc = 1
            continue
        ut = ut.replace(old, new, 1)
        print("patch-sweetlog: %s" % name)
    ufs.write_text(ut, errors="surrogateescape")
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
