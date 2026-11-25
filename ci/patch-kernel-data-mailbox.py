#!/usr/bin/env python3
"""HAKIOS-LAFA-DATA-VIA-MAILBOX: read/write the HFS data volume through the loader, always.

With handoff_to_cmd=1 (the only configuration that survives more than ~90 s) the kernel keeps the
UFS controller and reads through its own driver, but the data volume (the darwin partition the
boot tasks write /private/var into) fails to mount:

    HFS Plus from /dev/ufs_darwin_data: Invalid argument; a memory filesystem instead

while the same clean HFS+ image mounts fine in loader-served mode (ufs=2 without handoff).  The
data path is the one that switches the controller back and forth (ufs_native_out when a write
needs the firmware's list), so force the data volume's reads through the loader's mailbox: the
loader now serves block requests from its command loop as well (usb_wait -> block_poll), and the
HFS+ driver then sees exactly the bytes the loader-served mode gives it.

The write path (ufs_write) is left alone -- it is the port's window into the same partition.

Idempotent.

    python3 ci/patch-kernel-data-mailbox.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-DATA-VIA-MAILBOX"
OLD = "\t\treturn ufs_read((uint32_t)ufs_write_part, base + offset, p, len);"
NEW = ("\t\t/* " + MARK + ": the data volume goes through the loader's page even in native mode --\n"
       "\t\t * the native path switches the controller back and forth and HFS+ then mounts with EINVAL. */\n"
       "\t\treturn ufs_read_mailbox((uint32_t)ufs_write_part, base + offset, p, len);")


def find_driver(root: pathlib.Path):
    for cand in (
        root.parent.parent / "drivers/storage/hakios.ufs.c",
        root.parent.parent.parent / "hakios/drivers/storage/hakios.ufs.c",
    ):
        if cand.exists():
            return cand
    return None


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "xnu")
    f = find_driver(root)
    if f is None:
        print("patch-data-mailbox: MISS hakios.ufs.c")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-data-mailbox: already applied")
        return 0
    if OLD not in text:
        print("patch-data-mailbox: MISS ufs_data_io read")
        return 1
    text = text.replace(OLD, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-data-mailbox: the data volume's reads go through the loader")
    return 0


if __name__ == "__main__":
    sys.exit(main())
