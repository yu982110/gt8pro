#!/usr/bin/env python3
"""patch-init-rawtest.py -- prove from user space whether the ufs device takes writes.

The HFS+ mount of /dev/ufs_oplusreserve2 still fails with EROFS after the driver's writable flag
was forced true and the partition bounds were lifted, so the refusal is not in the two places the
source shows. Next layer: is the *device* writable at all from user space? init opens the raw
device read-write, writes 4 KiB at the end of the partition (never the header), reads it back and
reports PASS/FAIL with errno, all on screen. That separates "the device still refuses writes"
from "HFS refuses the volume while the device is fine".

    python3 ci/patch-init-rawtest.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-RAWTEST"

ANCHOR = '\tcheck(ok, "mount a memory filesystem on /private/var");\n'

NEW = ANCHOR + """	/*
	 * HAKIOS-RAWTEST: is /dev/ufs_oplusreserve2 writable at all? Write 4 KiB at the end of
	 * the partition (the header is untouched), read it back, report on screen.
	 */
	{
		static const char hakios_dev[] = "/dev/ufs_oplusreserve2";
		static char hakios_buf[4096];
		int hakios_fd;
		ssize_t hakios_n;
		int hakios_ok = 0;
		int hakios_err = 0;

		for (int i = 0; i < 4096; i++) {
			hakios_buf[i] = (char)(i * 7 + 3);
		}
		hakios_fd = open(hakios_dev, O_RDWR, 0);
		if (hakios_fd < 0) {
			hakios_err = errno;
		} else {
			off_t hakios_at = (off_t)(256 * 1024 * 1024 - 4096);

			if (pwrite(hakios_fd, hakios_buf, 4096, hakios_at) != 4096) {
				hakios_err = errno;
			} else {
				char hakios_back[4096];
				int same = 1;

				for (int i = 0; i < 4096; i++) {
					hakios_back[i] = 0;
				}
				if (pread(hakios_fd, hakios_back, 4096, hakios_at) != 4096) {
					hakios_err = errno;
				} else {
					for (int i = 0; i < 4096; i++) {
						if (hakios_back[i] != hakios_buf[i]) {
							same = 0;
							break;
						}
					}
					hakios_ok = same;
				}
			}
			close(hakios_fd);
		}
		printf("\\n\\n===== raw write to %s: %s (errno %d) =====\\n", hakios_dev,
		    hakios_ok ? "PASS" : "FAIL", hakios_err);
		printf("===== hold 5 s =====\\n\\n");
		usleep(5000000);
	}
"""


def main(root):
    for rel in ("src/init.c", "init.c"):
        f = pathlib.Path(root) / rel
        if f.exists():
            break
    else:
        print("patch-init-rawtest: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-rawtest: already applied")
        return 0
    if text.count(ANCHOR) != 1:
        print("patch-init-rawtest: MISS: the ramfs check line appears %d times"
              % text.count(ANCHOR))
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-rawtest: raw device write/read test on the console")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-rawtest.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
