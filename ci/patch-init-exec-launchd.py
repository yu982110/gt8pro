#!/usr/bin/env python3
"""patch-init-exec-launchd.py -- hand process 1 to the real launchd right after the switch root.

The switch root leaves the Darwin root mounted (its /sbin/launchd.darwin is there, 525088 bytes),
but /redwood/etc/rc is not where init looks for the hand-over, so init ran its tests and halted.
With root_apfs set, exec /sbin/launchd.darwin once the data volume is mounted, instead of
running the tests and the rc file this layout does not provide where they look.

    python3 ci/patch-init-exec-launchd.py <userland dir>
"""
import pathlib
import sys

MARK = "HAKIOS-EXEC-LAUNCHD"

ANCHOR = '''	if (boot_arg("root_apfs") != NULL) {
			flash_root(dev);
		}
'''


def main(root):
    for rel in ("src/init.c", "init.c"):
        f = pathlib.Path(root) / rel
        if f.exists():
            break
    else:
        print("patch-init-exec-launchd: init.c not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-init-exec-launchd: already applied")
        return 0
    # find the flash_root call and add the hand-over after it
    marker = "\tmount_writable();\n"
    if text.count(marker) != 1:
        print("patch-init-exec-launchd: MISS: the mount_writable call appears %d times" % text.count(marker))
        return 1
    add = marker + '''\t\t\t/*
\t\t\t * HAKIOS-EXEC-LAUNCHD: the switch root has happened; the Darwin root's own launchd is
\t\t\t * at /sbin/launchd.darwin. Hand over now, instead of going on with the tests and the
\t\t\t * rc file that this layout does not put where they look.
\t\t\t */
\t\t\tif (flash_root_mode) {
\t\t\t\tchar *hk_argv[] = { "/sbin/launchd.darwin", NULL };

\t\t\t\tprintf("\\n=== hand over to /sbin/launchd.darwin ===\\n");
\t\t\t\texecve("/sbin/launchd.darwin", hk_argv, envp);
\t\t\t\tprintf("  execve: %s\\n", strerror(errno));
\t\t\t}
'''
    text = text.replace(marker, add, 1)
    f.write_text(text, errors="surrogateescape")
    print("patch-init-exec-launchd: hands over after mount_writable (the data volume is mounted)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-init-exec-launchd.py <userland dir>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
