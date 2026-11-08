#!/usr/bin/env python3
"""patch-init-datavol.py -- mount the image's Data volume, switchable by boot-arg.

launchd's built-in boot task (restore-datapartition) does the task and then the kernel goes
silent: either the task itself hangs, or it fights with a mount this init made. To tell those
apart without rebuilding init every time, the mount is behind a boot-arg:

    hakios_datavol=0   do not mount it (ramfs on /private/var only)

The kernel's APFS mount takes "DEVICE[,c=N][,v=M]" (apfs_vfsops.c, "the container (from 0) and
the volume (by index)"), so the second volume of the image's container is:

    mount("apfs", "/System/Volumes/Data", 0, "/dev/md0,v=1")

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
		 * Data=...). launchd's restore-datapartition task needs a data volume and the
		 * kernel's apfs mount takes ",v=<volume index>". Boot-arg hakios_datavol=0
		 * leaves it unmounted, to tell a hang in that task from a mount conflict.
		 */
		struct stat dv;
		const char *dv_mode = boot_arg("hakios_datavol");

		if (dv_mode != NULL && dv_mode[0] == '0') {
			printf("  data volume: not mounted (hakios_datavol=0)\\n");
		} else if (stat("/System/Volumes/Data", &dv) != 0) {
			printf("  data volume: no /System/Volumes/Data to mount on\\n");
		} else if (mount("apfs", "/System/Volumes/Data", 0, (void *)"/dev/md0,v=1") == 0) {
			printf("  data volume on /System/Volumes/Data\\n");
		} else {
			printf("  data volume on /System/Volumes/Data: %s\\n", strerror(errno));
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
    print("patch-init-datavol: data volume mount behind hakios_datavol")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-datavol.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
