#!/usr/bin/env python3
"""patch-init-datavol.py -- mount the image's Data volume before launchd takes over.

launchd's built-in boot task (restore-datapartition) does the task, fails and retries forever:
there is no data volume. The kernel's APFS mount takes "DEVICE[,c=N][,v=M]" (see
apfs_vfsops.c, "the container (from 0) and the volume (by index)"), so the second volume of the
image's own container - volume 1, the Data volume built by mkapfs - can be mounted in init:

    mount("apfs", "/System/Volumes/Data", 0, "/dev/md0,v=1")

The mount point must exist in the System volume (read only), and this is best effort: failures
are printed, never fatal.

    python3 ci/patch-init-datavol.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-DATAVOL"

ANCHOR = """	if (!flash_root_mode) {
		/*
		 * HAKIOS-RAMFS:"""

NEW = """	if (!flash_root_mode) {
		/*
		 * HAKIOS-DATAVOL: the image's container has a second volume, Data (mkapfs
		 * Data=...). launchd's restore-datapartition task needs a data volume and retries
		 * for ever without one; the kernel's apfs mount takes ",v=<volume index>".
		 */
		struct stat dv;

		if (stat("/System/Volumes/Data", &dv) == 0) {
			if (mount("apfs", "/System/Volumes/Data", 0, (void *)"/dev/md0,v=1") == 0) {
				printf("  data volume on /System/Volumes/Data\\n");
			} else {
				printf("  data volume on /System/Volumes/Data: %s\\n", strerror(errno));
			}
		} else {
			printf("  data volume: no /System/Volumes/Data to mount on\\n");
		}
	}

"""


def main(root):
    f = pathlib.Path(root) / "src" / "init.c"
    if not f.exists():
        f = pathlib.Path(root) / "init.c"
    if not f.exists():
        print("patch-init-datavol: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-datavol: already applied")
        return 0
    if ANCHOR not in text:
        print("patch-init-datavol: MISS: the ramfs block is not there (apply ramfs first)")
        return 1
    text = text.replace(ANCHOR, NEW + ANCHOR, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-datavol: init mounts /dev/md0,v=1 on /System/Volumes/Data")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-datavol.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
