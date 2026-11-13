#!/usr/bin/env python3
"""patch-init-ufsreport.py -- hold the screen with the /private/var result, to be photographed.

The kernel's printf lines do not reach the persisted log (only the loader's own log and the user
console do), and init's output reaches neither - it lives on the screen only, and the writable
filesystem section scrolls past in well under a second. That is why the HFS+ question has taken
so long to answer: "HFS Plus from /dev/ufs_oplusreserve2: <error>" versus "kept between boots" is
printed on one line and gone.

This prints the result again, in a banner, and busy-waits five seconds so any photo or video frame
of that moment has it.

    python3 ci/patch-init-ufsreport.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-UFSREPORT"

ANCHOR = '\tcheck(ok, "mount a memory filesystem on /private/var");\n'

NEW = ANCHOR + """	/*
	 * HAKIOS-UFSREPORT: the one line that says whether /private/var is the HFS+ volume or a
	 * memory filesystem scrolls past in a fraction of a second. Repeat it and hold the screen.
	 */
	printf("\\n\\n===== /private/var is %s =====\\n",
	    var_kept ? "HFS+ (read-write, kept between boots)" : "MEMORY FS (the HFS+ mount failed)");
	printf("===== hask_ufsreport done, 5 s =====\\n\\n");
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
"""


def main(root):
    for rel in ("src/init.c", "init.c"):
        f = pathlib.Path(root) / rel
        if f.exists():
            break
    else:
        print("patch-init-ufsreport: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-ufsreport: already applied")
        return 0
    if text.count(ANCHOR) != 1:
        print("patch-init-ufsreport: MISS: the ramfs check line appears %d times"
              % text.count(ANCHOR))
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-ufsreport: /private/var result banner + 5 s hold")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-ufsreport.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
