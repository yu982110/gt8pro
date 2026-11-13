#!/usr/bin/env python3
"""patch-init-rawtest.py -- read the HFS+ volume header through /dev/ufs_darwin_data.

The synthetic darwin partition is served by the image's own partition, so whether the kernel's
/dev/ufs_darwin_data window starts where the HFS+ image was written is the whole question. This
reads the four bytes at offset 1024 of that device - the HFS+ volume signature 0x482b ("H+") when
the window is right - and prints them on the console, then holds the screen.

    python3 ci/patch-init-rawtest.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-RAWTEST"

ANCHOR = '\tcheck(ok, "mount a memory filesystem on /private/var");\n'

NEW = ANCHOR + """	/*
	 * HAKIOS-RAWTEST: does /dev/ufs_darwin_data start where the HFS+ image was written? The
	 * volume signature is the four bytes at offset 1024: 48 2b ("H+") when it does.
	 */
	{
		static const char hakios_dev[] = "/dev/ufs_darwin_data";
		unsigned char hakios_hdr[4] = { 0, 0, 0, 0 };
		int hakios_fd = open(hakios_dev, O_RDONLY, 0);
		int hakios_err = 0;
		int hakios_ok = 0;

		if (hakios_fd < 0) {
			hakios_err = errno;
		} else {
			if (pread(hakios_fd, hakios_hdr, 4, 1024) != 4) {
				hakios_err = errno;
			} else {
				hakios_ok = (hakios_hdr[0] == 0x48 && hakios_hdr[1] == 0x2b);
			}
			close(hakios_fd);
		}
		printf("\\n\\n===== darwin_data[1024] = %02x %02x %02x %02x: %s (errno %d) =====\\n",
		    hakios_hdr[0], hakios_hdr[1], hakios_hdr[2], hakios_hdr[3],
		    hakios_ok ? "HFS+ HERE" : "NOT HFS+", hakios_err);
		printf("===== hold 5 s =====\\n\\n");
		/* HAKIOS-HOLD: how long to hold the screen for a photo (boot-arg hakios_hold=SECS). */
	{
		const char *hakios_hold = boot_arg("hakios_hold");
		unsigned hakios_secs = 5;

		if (hakios_hold != NULL) {
			unsigned v = 0;

			for (const char *p = hakios_hold; *p >= '0' && *p <= '9'; p++) {
				v = v * 10 + (unsigned)(*p - '0');
			}
			if (v != 0 && v <= 120) {
				hakios_secs = v;
			}
		}
		usleep(hakios_secs * 1000000u);
	}
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
    print("patch-init-rawtest: reads the HFS+ signature through /dev/ufs_darwin_data")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-rawtest.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
