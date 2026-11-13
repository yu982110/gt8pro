#!/usr/bin/env python3
"""patch-init-flashroot.py -- let flash_root work when the boot root is read only.

flash_root() mounts the Darwin volume on "/flash", and the root here is the read only system
image, so "/flash" cannot be made: mount(2) then answers ENOENT for a missing mountpoint. This
mounts a memory filesystem over /private (which exists on the image) and uses /private/flash for
those paths instead, so flash_root can go on: the volume mount, the launchd job binds, the switch
root, the cryptexes.

    python3 ci/patch-init-flashroot.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-FLASHROOT-RAM"

START = '\tprintf("\\n-- the Darwin root volume on /dev/%s as the root --\\n", dev);\n'
END = '\tflash_root_mode = 1;\n'


def main(root):
    for rel in ("src/init.c", "init.c"):
        f = pathlib.Path(root) / rel
        if f.exists():
            break
    else:
        print("patch-init-flashroot: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-flashroot: already applied")
        return 0
    if text.count(START) != 1 or text.count(END) != 1:
        print("patch-init-flashroot: MISS: start %d end %d" % (text.count(START), text.count(END)))
        return 1
    i = text.index(START)
    j = text.index(END, i)
    body = text[i:j]
    if "/flash" not in body:
        print("patch-init-flashroot: MISS: no /flash in flash_root")
        return 1
    head = START + '''\t/*
\t * HAKIOS-FLASHROOT-RAM: the root here is the read only system image, so the paths below
\t * cannot be made on it. A memory filesystem over /private gives flash_root somewhere to
\t * mount the Darwin volume and to put /private/preboot for the switch root.
\t */
\t(void)mount("ramfs", "/private", 0, NULL);
\t(void)mkdir("/private/flash", 0755);
\t(void)mkdir("/private/preboot", 0755);
\t(void)mkdir("/private/var", 0755);
'''
    body = body[len(START):].replace("/flash", "/private/flash")
    text = text[:i] + head + body + text[j:]
    f.write_text(text, errors="surrogateescape")
    print("patch-init-flashroot: flash_root uses /private/flash on a ramfs")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-flashroot.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
