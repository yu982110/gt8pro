#!/usr/bin/env python3
"""patch-init-varseed.py -- the accounts of a Darwin system on the writable data volume.

What fails on the phone, from the boot log:

    (user/501/com.apple.SpringBoard [199]) <Error>: Service could not initialize:
      getpwnam("mobile"), error 0x2 - No such file or directory
    (user/501/com.apple.SpringBoard [199]) <Notice>: xpcproxy exited due to exit(78)

and the same for every other job of the user/501 domain (logd, tccd, timed, ...): there is no
account for uid 501 on the data volume. Darwin keeps its local user directory on the data
volume, /private/var/db/dslocal/nodes/Default/{users,groups}/*.plist, which a boot of ours
makes from nothing -- init's mount_writable() builds the directories of /private/var from the
image's etc/var.skel, and that skeleton carries no dslocal node. The read-only system image
cannot be written at boot, and the account cannot wait for launchd (the user/501 domain is
created while launchd loads the job table, before any of our jobs could run).

So the records travel in our payload -- userland/root/varseed/, that is /redwood/varseed in
the booted system -- and this patch adds to init, at the end of mount_writable() (the data
volume is mounted and the skeleton's directories are made), the copy into
/private/var/db/dslocal/... . That point is before the hand-over to launchd on both boot
paths: a flash boot execs launchd straight after mount_writable() returns (patch-init-exec-
launchd.py), a RAM boot goes on to run the job list from /etc/rc.

The block also prints what the boot sees of the user database, because Darwin's lookup is not
one file (docs/USERDB-PLAN.md): libsystem_info asks usermanagerd first (Mach service
com.apple.system.libinfo.muser, which the boot task "usermanagerd --init" provides) and has a
"file" module that reads the flat /etc/master.passwd this image already carries. Which of the
two answered a boot is in the console log:

  - "/private/etc/master.passwd: ... mobile: yes" -- the flat database is there for the file
    module (the read-only system image was built with it);
  - "usermanagerd --init wrote N bytes" + the lines of /private/var/logs/usermanagerd_init.log
    -- what the usermanagerd of the *previous* boot said before it died (the log lives on the
    kept HFS data volume; the current boot's usermanagerd runs after this point, when launchd
    takes over);
  - "users/mobile.plist: a binary plist, read back" -- the seed itself landed intact.

Place in the chain: anywhere among our init patches -- the anchors are in mount_writable()
itself and no other patch touches them. After patch-init-exec-launchd.py is fine.

    python3 ci/patch-init-varseed.py <userland dir>

Exit: 0 applied (or already applied), 1 MISS, 2 usage.
"""
import pathlib
import sys

MARK = "HAKIOS-VARSEED"

# mount_writable() is where /private/var is mounted and etc/var.skel is turned into
# directories: the seed goes right after the skeleton's check, together with the two
# directories of the dslocal node. Anchors are unique in the upstream file.
FN_ANCHOR = "static void\nmount_writable(void)\n{\n"

CALL_ANCHOR = '\tcheck(ok && made > 50, "create the directories of the data volume");\n'

HELPER = '''/*
 * HAKIOS-VARSEED: the accounts of a Darwin system, put in place on the data volume right after
 * the directories of the skeleton are made -- and before the hand-over to launchd.
 *
 * Without an account for "mobile" (uid 501) launchd cannot start the jobs of its user/501
 * domain: each of them dies in xpcproxy with
 *     Service could not initialize: getpwnam("mobile"), error 0x2 - No such file or directory
 * and exit(78). The local node of Darwin's user directory, /private/var/db/dslocal, belongs on
 * the data volume and not in the read-only system image, so it is made here from the files our
 * image carries in varseed/ (userland/root/varseed in this tree).
 *
 * The image is read through in_image(): at this point /redwood is our image (the RAM disk stage0
 * mounted on it, or the copy on the flash) and /private/var is mounted and writable.
 *
 * Darwin's lookup is not one database (docs/USERDB-PLAN.md): libsystem_info asks usermanagerd
 * (Mach service com.apple.system.libinfo.muser) first and has a "file" module over the flat
 * /etc/master.passwd. What the boot itself sees of both is printed below, so a boot that still
 * fails getpwnam says which of the two is missing.
 */
static int
varseed_have(const char *hay, const char *needle)
{
	size_t n = strlen(needle);

	if (n == 0) {
		return 1;
	}
	for (const char *p = hay; *p; p++) {
		if (strncmp(p, needle, n) == 0) {
			return 1;
		}
	}
	return 0;
}

/* A directory of the seed, made when missing; EEXIST (17) is what a kept volume gives. */
static int
varseed_dir(const char *path)
{
	int ok = mkdir(path, 0700) == 0 || errno == 17;

	if (ok) {
		ok = chmod(path, 0700) == 0 && chown(path, 0, 0) == 0;
	}
	if (!ok) {
		printf("  varseed: %s: %s\\n", path, strerror(errno));
	}
	return ok;
}

static void
varseed(void)
{
	/* the parents of the node and its two directories; the skeleton has none of them */
	static const char *const dirs[] = {
		"/private/var/db",
		"/private/var/db/dslocal",
		"/private/var/db/dslocal/nodes",
		"/private/var/db/dslocal/nodes/Default",
		"/private/var/db/dslocal/nodes/Default/users",
		"/private/var/db/dslocal/nodes/Default/groups",
	};
	/* in our image (varseed/), on the data volume, mode */
	static const struct {
		const char *from;
		const char *to;
		int mode;
	} seeds[] = {
		{ "/varseed/db/dslocal/nodes/Default/users/mobile.plist",
		  "/private/var/db/dslocal/nodes/Default/users/mobile.plist", 0600 },
		{ "/varseed/db/dslocal/nodes/Default/users/root.plist",
		  "/private/var/db/dslocal/nodes/Default/users/root.plist", 0600 },
		{ "/varseed/db/dslocal/nodes/Default/groups/mobile.plist",
		  "/private/var/db/dslocal/nodes/Default/groups/mobile.plist", 0600 },
		{ "/varseed/db/dslocal/nodes/Default/groups/wheel.plist",
		  "/private/var/db/dslocal/nodes/Default/groups/wheel.plist", 0600 },
		{ "/varseed/db/dslocal/nodes/Default/groups/staff.plist",
		  "/private/var/db/dslocal/nodes/Default/groups/staff.plist", 0600 },
		{ "/varseed/db/dslocal/nodes/Default/groups/admin.plist",
		  "/private/var/db/dslocal/nodes/Default/groups/admin.plist", 0600 },
	};
	static char text[4096], head[8];
	struct stat st;
	ssize_t n, got;
	int ok = 1, fd, made = 0, put = 0, there = 0;
	unsigned i;

	printf("\\n-- varseed: the user database of the data volume --\\n");
	for (i = 0; i < sizeof(dirs) / sizeof(dirs[0]); i++) {
		if (varseed_dir(dirs[i])) {
			made++;
		} else {
			ok = 0;
		}
	}
	for (i = 0; i < sizeof(seeds) / sizeof(seeds[0]); i++) {
		if (var_kept && lstat(seeds[i].to, &st) == 0) {
			/* the kept volume has it: what Darwin made of the record since is what counts */
			printf("  varseed: %s: there already\\n", seeds[i].to);
			there++;
			continue;
		}
		if (copy_file(in_image(seeds[i].from), seeds[i].to, seeds[i].mode, 0, 0)) {
			put++;
		} else {
			printf("  varseed: %s: %s\\n", seeds[i].to, strerror(errno));
			ok = 0;
		}
	}
	printf("  varseed: %d directories, %d records copied, %d already there\\n", made, put, there);
	/* the record itself, read back: a directory service record is a binary plist */
	fd = open("/private/var/db/dslocal/nodes/Default/users/mobile.plist", O_RDONLY, 0);
	got = -1;
	if (fd >= 0) {
		got = read(fd, head, sizeof(head));
		close(fd);
	}
	if (got == (ssize_t)sizeof(head) && memcmp(head, "bplist00", sizeof(head)) == 0) {
		printf("  varseed: users/mobile.plist: a binary plist, read back\\n");
	} else {
		printf("  varseed: users/mobile.plist: not a binary plist\\n");
		ok = 0;
	}
	check(ok, "the accounts of the data volume (mobile, uid 501)");

	/*
	 * What this boot has of the two ways Darwin can answer a user lookup: the flat database in
	 * the read-only system image (libsystem_info's "file" module), and the previous boot's log of
	 * usermanagerd, the server of com.apple.system.libinfo.muser (it runs as a launchd boot task,
	 * after this point, so only a log kept on the data volume is readable here).
	 */
	n = read_whole("/private/etc/master.passwd", text, sizeof(text));
	if (n >= 0) {
		printf("  varseed: /private/etc/master.passwd: %ld bytes, mobile: %s\\n", (long)n,
		    varseed_have(text, "mobile:") ? "yes" : "NO");
	} else {
		printf("  varseed: /private/etc/master.passwd: %s\\n", strerror(errno));
	}
	n = read_whole("/private/etc/group", text, sizeof(text));
	printf("  varseed: /private/etc/group: %s\\n", n >= 0 ? "there" : strerror(errno));
	if (stat("/usr/libexec/usermanagerd", &st) == 0) {
		printf("  varseed: /usr/libexec/usermanagerd: there, %ld bytes\\n", (long)st.st_size);
	} else {
		printf("  varseed: /usr/libexec/usermanagerd: %s\\n", strerror(errno));
	}
	/*
	 * iOS 16.5 has no opendirectoryd: the local-node DirectoryService of macOS is not there
	 * at all (no binary, no job in launchd's 530-entry cache), and the libinfo records come
	 * from usermanagerd's own account table. The line says so in the log, so a boot that still
	 * fails getpwnam is not blamed on a missing opendirectoryd job.
	 */
	if (stat("/usr/libexec/opendirectoryd", &st) == 0) {
		printf("  varseed: /usr/libexec/opendirectoryd: there, %ld bytes\\n", (long)st.st_size);
	} else {
		printf("  varseed: /usr/libexec/opendirectoryd: %s (not part of iOS 16.5)\\n",
		    strerror(errno));
	}
	n = read_whole("/private/var/logs/usermanagerd_init.log", text, sizeof(text));
	if (n <= 0) {
		printf("  varseed: usermanagerd --init log: %s\\n", n == 0 ? "empty" : strerror(errno));
	} else {
		char *line = text;

		printf("  varseed: usermanagerd --init wrote %ld bytes last boot:\\n", (long)n);
		while (*line) {
			char *end = strchr(line, '\\n');

			if (end) {
				*end = 0;
			}
			printf("    umlog: %s\\n", line);
			if (!end) {
				break;
			}
			line = end + 1;
		}
	}
}

'''

CALL = '''\t/*
\t * HAKIOS-VARSEED: an account for uid 501 and the groups of the data volume, before
\t * launchd gets process 1 (the user/501 domain is made while it loads the job table).
\t */
\tvarseed();
'''


def main(root):
    for rel in ("src/init.c", "init.c"):
        f = pathlib.Path(root) / rel
        if f.exists():
            break
    else:
        print("patch-init-varseed: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-varseed: already applied")
        return 0
    if text.count(FN_ANCHOR) != 1:
        print("patch-init-varseed: MISS: mount_writable() is not there once (%d times)"
              % text.count(FN_ANCHOR))
        return 1
    if text.count(CALL_ANCHOR) != 1:
        print("patch-init-varseed: MISS: the skeleton's check is not there once (%d times)"
              % text.count(CALL_ANCHOR))
        return 1
    text = text.replace(FN_ANCHOR, HELPER + FN_ANCHOR, 1)
    text = text.replace(CALL_ANCHOR, CALL_ANCHOR + CALL, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-varseed: /private/var/db/dslocal seeded from varseed/, after the mounts")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-varseed.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
