#!/usr/bin/env python3
"""HAKIOS-UFS-DIAG: send the UFS driver's reports into the loader's live ring.

The ticker stalls of tens of seconds match CMD_TIMEOUT_NS (5 s) times a handful of retries, but
the driver's own `printf` reports go to the kernel console, which never reaches the loader's live
ring -- so a stall leaves no trace in the log we read back.

This patch adds a small vsnprintf wrapper and redirects the file's `printf` to it with a macro, so
every report in sweet_ufs.c lands in the ring with its original text, without touching the call
sites.

Idempotent; exits 0 when already applied, 1 when sweet_ufs.c is not found.

    python3 ci/patch-kernel-ufs-diag.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-UFS-DIAG"

BLOCK = """
/* %MARK%: the driver's reports must reach the loader's ring, not the console. */
extern void loader_log_line(const char *line);
#include <stdarg.h>
static void
lafa_ufs_note(const char *fmt, ...)
{
	char lafa_line[160];
	va_list lafa_ap;

	va_start(lafa_ap, fmt);
	vsnprintf(lafa_line, sizeof(lafa_line), fmt, lafa_ap);
	va_end(lafa_ap);
	loader_log_line(lafa_line);
}
#define printf lafa_ufs_note
"""


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    # the built driver is synced from hakios/drivers/storage/hakios.ufs.c (outside the xnu tree);
    # patch the source first, or the sync in make-kernel.sh overwrites it
    src = root.parent.parent.parent / "drivers/storage/hakios.ufs.c"
    f = src if src.exists() else next(root.rglob("hakios.ufs.c"), None)
    if f is None:
        f = next(root.rglob("sweet_ufs.c"), None)
    if f is None:
        print("patch-ufs-diag: MISS sweet_ufs.c")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-ufs-diag: already applied")
        return 0
    # after the last #include line, so stdarg and the prototypes are in scope
    lines = text.split("\n")
    last = 0
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#include"):
            last = i
    lines.insert(last + 1, BLOCK.replace("%MARK%", MARK))
    text = "\n".join(lines)
    f.write_text(text, encoding="utf-8")
    print("patch-ufs-diag: printf redirected into the live ring (after line %d)" % (last + 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
