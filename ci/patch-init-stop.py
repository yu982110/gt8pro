#!/usr/bin/env python3
"""patch-init-stop.py -- freeze right after flash_root, so its output can be read.

flash_root() prints what the installed-root flow found (the Darwin volume mount, the switch root,
the cryptexes), but init goes on and the console scrolls it away. With boot-arg
hakios_stop_early=1 this freezes at the end of flash_root, leaving that output on the screen for a
photograph.

    python3 ci/patch-init-stop.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-STOP-EARLY"

ANCHOR = """		if (dev != NULL) {
			flash_root(dev);
		}
"""

NEW = ANCHOR + """		/* HAKIOS-STOP-EARLY: hakios_stop_early=1 freezes here, so flash_root's output stays. */
		{
			const char *hke = boot_arg("hakios_stop_early");

			if (hke != NULL && hke[0] == '1') {
				printf("\\n=== frozen after flash_root ===\\n");
				for (;;) {
					usleep(1000000);
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
        print("patch-init-stop: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-stop: already applied")
        return 0
    if text.count(ANCHOR) != 1:
        print("patch-init-stop: MISS: the flash_root line appears %d times" % text.count(ANCHOR))
        return 1
    text = text.replace(ANCHOR, NEW, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-stop: freezes after flash_root with hakios_stop_early=1")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-stop.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
