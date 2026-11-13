#!/usr/bin/env python3
"""patch-init-preboom.py -- keep the boot image available as /redwood across the switch root.

flash_root ends with bind_dir("/private/preboot/redwood", "/redwood"), and that failed here: the
payload this init runs from (mounted at /redwood) did not end up under /private/preboot when the
old root was moved there, so /redwood/etc/rc could not be read and init never handed over to
launchd. This makes /private/preboot/redwood on the Darwin volume before the switch root, and binds
the payload's /redwood into it, so the bind after the switch finds it.

    python3 ci/patch-init-preboom.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-PREBOOT-REDWOOD"

ANCHOR = '\t(void)mkdir("/private/preboot", 0755);\n'

ADD = '''\t/*
\t * HAKIOS-PREBOOT-REDWOOD: the switch root moves this boot's old root to /private/preboot, and
\t * flash_root then binds /private/preboot/redwood back on /redwood (this image, that is what
\t * runs launchd). Make that path on the Darwin volume now and bind the payload's /redwood into
\t * it, so it is there after the switch.
\t */
\t(void)synthetic_dir("/private/flash/private");
\t(void)synthetic_dir("/private/flash/private/preboot");
\t(void)synthetic_dir("/private/flash/private/preboot/redwood");
\t(void)bind_dir("/redwood", "/private/flash/private/preboot/redwood");
'''


def main(root):
    for rel in ("src/init.c", "init.c"):
        f = pathlib.Path(root) / rel
        if f.exists():
            break
    else:
        print("patch-init-preboom: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-preboom: already applied")
        return 0
    if text.count(ANCHOR) != 1:
        print("patch-init-preboom: MISS: the preboot mkdir appears %d times" % text.count(ANCHOR))
        return 1
    text = text.replace(ANCHOR, ANCHOR + ADD, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-preboom: /private/preboot/redwood is made and bound before the switch root")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-preboom.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
