#!/usr/bin/env python3
"""patch-init-rawtest.py -- record the HFS+ probe result on the storage, for the host to read.

The screen is unreliable (the loader's snapshots, scrolling, the phone rebooting) and the
kernel's own console does not always reach the persisted log, so neither photos nor logs have
answered the one question left: does /dev/ufs_darwin_data start where the HFS+ image was written?

So init reads the four bytes at offset 1024 of that device - the HFS+ volume signature 48 2b
("H+") when the window is right - and writes a one line report into the free space of userdata
at the end of the darwin window (1 GiB - 4 KiB), which the host reads with dd from Android:

    ===== darwin_data[1024] = 48 2b 00 04: HFS+ HERE (errno 0) var 1 =====\n    (the write to /dev/ufs_userdata was refused by the write guard; the darwin window is writable)

It also prints it, but the point is the record. The write is best effort: a failure is printed
and otherwise ignored.

    python3 ci/patch-init-rawtest.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-RAWTEST"

ANCHOR = '\tcheck(ok, "mount a memory filesystem on /private/var");\n'

NEW = ANCHOR + """	/*
	 * HAKIOS-RAWTEST: read the HFS+ signature through /dev/ufs_darwin_data (offset 1024), and
	 * record it in userdata at 340 GiB so the host can read it with dd - no camera needed.
	 */
	{
		static const char hakios_dev[] = "/dev/ufs_darwin_data";
		static const char hakios_rec_dev[] = "/dev/ufs_darwin_data";
		static const char hakios_rec_at[] = "HAKIOSREPORT ";
		unsigned char hakios_hdr[4] = { 0, 0, 0, 0 };
		char hakios_rec[64];
		int hakios_fd = open(hakios_dev, O_RDONLY, 0);
		int hakios_err = 0;
		int hakios_ok = 0;
		int hakios_len;

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
		printf("\\n\\n===== darwin_data[1024] = %02x %02x %02x %02x: %s (errno %d) var %d =====\\n",
		    hakios_hdr[0], hakios_hdr[1], hakios_hdr[2], hakios_hdr[3],
		    hakios_ok ? "HFS+ HERE" : "NOT HFS+", hakios_err, var_kept);

		/* "HAKIOSREPORT m=482b0004 e=0 v=1\\n", written at the end of the darwin window */
		hakios_len = 0;
		for (const char *p = hakios_rec_at; *p != 0; p++) {
			hakios_rec[hakios_len++] = *p;
		}
		hakios_rec[hakios_len++] = 'm';
		hakios_rec[hakios_len++] = '=';
		for (int i = 0; i < 4; i++) {
			static const char hex[] = "0123456789abcdef";

			hakios_rec[hakios_len++] = hex[hakios_hdr[i] >> 4];
			hakios_rec[hakios_len++] = hex[hakios_hdr[i] & 0xf];
		}
		hakios_rec[hakios_len++] = ' ';
		hakios_rec[hakios_len++] = 'e';
		hakios_rec[hakios_len++] = '=';
		hakios_rec[hakios_len++] = (char)('0' + (hakios_err % 10));
		hakios_rec[hakios_len++] = ' ';
		hakios_rec[hakios_len++] = 'v';
		hakios_rec[hakios_len++] = '=';
		hakios_rec[hakios_len++] = (char)('0' + (var_kept ? 1 : 0));
		hakios_rec[hakios_len++] = '\\n';

		hakios_fd = open(hakios_rec_dev, O_WRONLY, 0);
		if (hakios_fd < 0) {
			printf("  record: open %s: %s\\n", hakios_rec_dev, strerror(errno));
		} else {
			off_t hakios_at = (off_t)((1 << 30) - 4096);      /* the end of the darwin window */

			if (pwrite(hakios_fd, hakios_rec, (size_t)hakios_len, hakios_at) != hakios_len) {
				printf("  record: pwrite: %s\\n", strerror(errno));
			} else {
				printf("  record: written at 340 GiB (%d bytes)\\n", hakios_len);
			}
			close(hakios_fd);
		}
		usleep(200000);
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
    print("patch-init-rawtest: records the HFS+ probe at the end of the darwin window")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-rawtest.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
