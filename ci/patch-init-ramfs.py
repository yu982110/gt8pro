#!/usr/bin/env python3
"""patch-init-ramfs.py -- fallback writable /private/var, behind a boot-arg.

launchd's boot tasks want a writable /private/var. Two ways to give it one:

  * var_hfs=<partition> - init's own mount_data_volume() mounts a journaled HFS+ volume from
    the phone's flash there and keeps it between boots (the reference port's arrangement);
  * RAMFS - a memory filesystem, empty at every boot: enough for a first boot, and the fallback
    when the HFS+ volume does not mount.

This patch adds the RAMFS fallback, off by default (hakios_ramfs=1 turns it on) so an HFS+ test
run is not shadowed by it. When it is on, the mounts are best effort and reported on the console.

    python3 ci/patch-init-ramfs.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-RAMFS"

ANCHOR = """	if (!flash_root_mode) {
"""

NEW = """	if (!flash_root_mode) {
		/*
		 * HAKIOS-RAMFS: launchd's boot tasks want a writable /private/var; the image is
		 * read only. With hakios_ramfs=1, mount a memory filesystem over it (empty at
		 * every boot). Default off: var_hfs= gives /private/var a kept HFS+ volume, and
		 * this must not shadow it. Reported so a failure is visible on the console.
		 */
		const char *ramfs_mode = boot_arg("hakios_ramfs");
		struct stat rst;

		if (ramfs_mode == NULL || ramfs_mode[0] == '0') {
			printf("  ramfs: off (hakios_ramfs=1 turns the fallback on)\\n");
		} else {
			if (stat("/private/var", &rst) == 0) {
				if (mount("ramfs", "/private/var", 0, NULL) == 0) {
					printf("  ramfs on /private/var\\n");
				} else {
					printf("  ramfs on /private/var: %s\\n", strerror(errno));
				}
			}
			if (stat("/System/Volumes/Data", &rst) == 0) {
				if (mount("ramfs", "/System/Volumes/Data", 0, NULL) == 0) {
					printf("  ramfs on /System/Volumes/Data\\n");
				} else {
					printf("  ramfs on /System/Volumes/Data: %s\\n", strerror(errno));
				}
			}
		}
	}

"""


def main(root):
    f = pathlib.Path(root) / "src" / "init.c"
    if not f.exists():
        f = pathlib.Path(root) / "init.c"
    if not f.exists():
        print("patch-init-ramfs: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-ramfs: already applied")
        return 0
    if ANCHOR not in text:
        print("patch-init-ramfs: MISS: the launchd exec / datavol block is not where expected")
        return 1
    text = text.replace(ANCHOR, NEW + ANCHOR, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-ramfs: ramfs behind hakios_ramfs, off by default")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-ramfs.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
