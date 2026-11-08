#!/usr/bin/env python3
"""patch-init-ramfs.py -- give launchd writable space before it takes over.

The port boots into Darwin's launchd (the cache loads, the bootstrapper comes up), and then
launchd's first boot task - (restore-datapartition) - ends in a userspace reboot because there
is no data volume and /private/var cannot be written: the image is read only.

The kernel has RAMFS. Mounting it over /private/var (and /System/Volumes/Data when that
directory exists) before the exec gives launchd a writable place to put its state without
changing the image. Both mounts are best-effort and are reported on the console.

    python3 ci/patch-init-ramfs.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-RAMFS"

# The exec of Darwin's launchd happens in the tail of init's main flow, after the checks.
ANCHOR = """	if (flash_root_mode && strcmp(argv[1], "/sbin/launchd.darwin") == 0) {
"""

NEW = """	if (!flash_root_mode) {
		/*
		 * HAKIOS-RAMFS: launchd's boot tasks need a writable /private/var (and a data
		 * volume); the image is read only. RAMFS over them is enough for the first boot
		 * task and costs no image space. Reported so a failure is visible on the console.
		 */
		struct stat st;

		if (stat("/private/var", &st) == 0) {
			if (mount("ramfs", "/private/var", 0, NULL) == 0) {
				printf("  ramfs on /private/var\\n");
			} else {
				printf("  ramfs on /private/var: %s\\n", strerror(errno));
			}
		}
		if (stat("/System/Volumes/Data", &st) == 0) {
			if (mount("ramfs", "/System/Volumes/Data", 0, NULL) == 0) {
				printf("  ramfs on /System/Volumes/Data\\n");
			} else {
				printf("  ramfs on /System/Volumes/Data: %s\\n", strerror(errno));
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
        print("patch-init-ramfs: MISS: the launchd exec is not where expected")
        return 1
    text = text.replace(ANCHOR, NEW + ANCHOR, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-ramfs: init mounts RAMFS on /private/var before launchd")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-ramfs.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
