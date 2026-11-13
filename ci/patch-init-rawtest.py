#!/usr/bin/env python3
"""patch-init-rawtest.py -- report the data volume and /usr state where the host can read it.

The screen is unreliable (the loader's snapshots, scrolling, the phone rebooting) and the kernel
console does not always reach the persisted log, so init reports what only it can see into places
the host can read without a camera:

  - reads the four bytes at offset 1024 of /dev/ufs_darwin_data: the HFS+ volume signature is
    48 2b ("H+") when the window starts where the image was written;
  - reads the first four bytes of /usr/libexec/HorizonSetup: whether the /usr payload volume is
    in place, which is what launchd's restore-datapartition boot task execs;
  - prints the boot arguments launchd reads to decide whether this is a RAM disk (rd) and the
    boot mode, and the /private/var result;
  - writes one line with all of it into /dev/klog (when this Darwin has one) and to the end of
    the darwin window, which the host reads with dd;
  - hakios_stop=1 freezes the machine here, so the screen can be photographed at rest.

    python3 ci/patch-init-rawtest.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-RAWTEST"

ANCHOR = '\tread_whole(in_image("/etc/var.skel"), skeleton, sizeof(skeleton));\n'

NEW = ANCHOR + """	/*
	 * HAKIOS-RAWTEST: what launchd reads to decide whether this is a RAM disk, and what the
	 * restore-datapartition boot task needs.
	 */
	{
		static const char hk_hs[] = "/usr/libexec/HorizonSetup";
		static const char hk_rd_dev[] = "/dev/ufs_darwin_data";
		static const char hk_rec_dev[] = "/dev/ufs_darwin_data";
		static const char hk_rec_at[] = "HAKIOSREPORT ";
		const char *hk_rd = boot_arg("rd");
		const char *hk_bm = boot_arg("boot-mode");
		unsigned char hk_hdr[4] = { 0, 0, 0, 0 };
		unsigned char hk_ism[4] = { 0, 0, 0, 0 };
		char hk_rec[96];
		int hk_err = 0, hk_ios = -1, hk_len = 0, hk_fd;

		/* HAKIOS-RAWTEST: which device nodes the kernel made, under which name. */
		{
			static const char *const hk_nodes[] = {
				"/dev/ufs_rootapfs", "/dev/rufs_rootapfs", "/dev/ufs_dinfo",
				"/dev/ufs_last_parti", "/dev/ufs_darwin", "/dev/ufs_darwin_data",
			};
			struct stat hk_st;

			for (unsigned hk_i = 0; hk_i < sizeof(hk_nodes) / sizeof(hk_nodes[0]); hk_i++) {
				printf("  node %s: %s\\n", hk_nodes[hk_i],
				    stat(hk_nodes[hk_i], &hk_st) == 0 ? "there" : strerror(errno));
			}
		}
		/* HAKIOS-RAWTEST: is the rootapfs device readable, and does it begin with an APFS container? */
		{
			int hk_afd = open("/dev/ufs_rootapfs", O_RDONLY, 0);

			if (hk_afd < 0) {
				printf("  rootapfs open: %s\\n", strerror(errno));
			} else {
				unsigned char hk_ab[4] = { 0, 0, 0, 0 };

				if (pread(hk_afd, hk_ab, 4, 32) != 4) {
					printf("  rootapfs read: %s\\n", strerror(errno));
				} else {
					printf("  rootapfs[32] = %02x %02x %02x %02x (APFS NXSB = 4e 58 53 42)\\n",
					    hk_ab[0], hk_ab[1], hk_ab[2], hk_ab[3]);
				}
				close(hk_afd);
			}
		}
		/* HAKIOS-RAWTEST: which mount spec the APFS driver takes for the rootapfs device. */
		{
			static const char *const hk_specs[] = {
				"/dev/ufs_rootapfs", "/dev/ufs_rootapfs,v=0",
				"/dev/ufs_rootapfs,c=0", "/dev/ufs_rootapfs,c=0,v=0",
			};

			(void)mkdir("/flash", 0755);
			for (unsigned hk_s = 0; hk_s < sizeof(hk_specs) / sizeof(hk_specs[0]); hk_s++) {
				int hk_me = mount("apfs", "/flash", MNT_RDONLY, (void *)hk_specs[hk_s]);

				printf("  apfs mount %s: %s\\n", hk_specs[hk_s],
				    hk_me == 0 ? "OK" : strerror(errno));
				if (hk_me == 0) {
					(void)unmount("/flash", MNT_FORCE);
				}
			}
		}
		printf("\\n  rd = %s, boot-mode = %s\\n", hk_rd ? hk_rd : "(null)", hk_bm ? hk_bm : "(null)");

		hk_fd = open(hk_rd_dev, O_RDONLY, 0);
		if (hk_fd < 0) {
			hk_err = errno;
		} else {
			if (pread(hk_fd, hk_hdr, 4, 1024) != 4) {
				hk_err = errno;
			}
			close(hk_fd);
		}
		hk_fd = open(hk_hs, O_RDONLY, 0);
		if (hk_fd < 0) {
			printf("  /usr payload: HorizonSetup MISSING (%s)\\n", strerror(errno));
		} else {
			if (pread(hk_fd, hk_ism, 4, 0) == 4) {
				hk_ios = 1;
				printf("  /usr payload: HorizonSetup %02x %02x %02x %02x\\n",
				    hk_ism[0], hk_ism[1], hk_ism[2], hk_ism[3]);
			} else {
				printf("  /usr payload: HorizonSetup unreadable (%s)\\n", strerror(errno));
			}
			close(hk_fd);
		}
		printf("===== darwin_data[1024] = %02x %02x %02x %02x (errno %d), var %d =====\\n",
		    hk_hdr[0], hk_hdr[1], hk_hdr[2], hk_hdr[3], hk_err, var_kept);

		/* "HAKIOSREPORT m=482b0004 r=md0 b=- u=1 v=1\\n" */
		for (const char *p = hk_rec_at; *p != 0; p++) {
			hk_rec[hk_len++] = *p;
		}
		for (const char *p = hk_rec_at; *p != 0; p++) {
			hk_rec[hk_len++] = *p;
		}

		{
			static const char hx[] = "0123456789abcdef";

			hk_rec[hk_len++] = 'm';
			hk_rec[hk_len++] = '=';
			for (int i = 0; i < 4; i++) {
				hk_rec[hk_len++] = hx[hk_hdr[i] >> 4];
				hk_rec[hk_len++] = hx[hk_hdr[i] & 0xf];
			}
			hk_rec[hk_len++] = ' ';
			hk_rec[hk_len++] = 'r';
			hk_rec[hk_len++] = '=';
			for (const char *p = hk_rd; p != NULL && *p != 0 && hk_len < 60; p++) {
				hk_rec[hk_len++] = *p;
			}
			hk_rec[hk_len++] = ' ';
			hk_rec[hk_len++] = 'u';
			hk_rec[hk_len++] = '=';
			hk_rec[hk_len++] = (char)('0' + (hk_ios == 1 ? 1 : 0));
			hk_rec[hk_len++] = ' ';
			hk_rec[hk_len++] = 'v';
			hk_rec[hk_len++] = '=';
			hk_rec[hk_len++] = (char)('0' + (var_kept ? 1 : 0));
			hk_rec[hk_len++] = '\\n';
		}
		hk_fd = open("/dev/klog", O_WRONLY, 0);
		if (hk_fd >= 0) {
			(void)write(hk_fd, hk_rec, (size_t)hk_len);
			close(hk_fd);
		}
		hk_fd = open(hk_rec_dev, O_WRONLY, 0);
		if (hk_fd >= 0) {
			if (pwrite(hk_fd, hk_rec, (size_t)hk_len, (off_t)((1 << 30) - 4096)) != hk_len) {
				printf("  record: %s\\n", strerror(errno));
			}
			close(hk_fd);
		}
		/* HAKIOS-STOP: hakios_stop=1 freezes here, so the screen can be photographed at rest. */
		{
			const char *hk_stop = boot_arg("hakios_stop");

			if (hk_stop != NULL && hk_stop[0] == '1') {
				for (;;) {
					usleep(1000000);
				}
			}
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
        print("patch-init-rawtest: MISS: the var skeleton line appears %d times"
              % text.count(ANCHOR))
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-rawtest: reports rd, /usr and the data volume, and can freeze")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-rawtest.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
