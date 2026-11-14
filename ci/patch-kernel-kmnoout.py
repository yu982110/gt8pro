#!/usr/bin/env python3
"""patch-kernel-kmnoout.py -- let user space write /dev/console without painting the panel.

Every daemon's stdout is the console, and this port's console is the km device.  kmoutput() copies
the bytes to the loader's ring first (which is how the log shows launchd's lines) and then hands the
same bytes to console_write(), i.e. to the video console.  When the screen is full, that console
scrolls -- and the port's own note in xnu/hakios.patch (osfmk/console/video_console.c) says a scroll
reads the framebuffer back "one bus access per word -- seconds per line on a phone-sized screen".

So the writer of the last line on screen never returns: the process that wrote it blocks in
kmoutput(), its parent (launchd) waits for it, and the boot stops exactly where the log stops.  This
is why patching PE_kputc (patch-kernel-noserial.py, patch-kernel-novideocon.py) changed nothing:
user space does not go through PE_kputc at all.

The panel does not need this path: the loader paints it from the ring.  So keep the ring write and
drop the console write.

    python3 ci/patch-kernel-kmnoout.py <xnu tree>

Idempotent (MARK), and the guard is turned into `if (0)` rather than deleting lines, so the braces
and the rest of kmoutput() stay exactly as they were.
"""
import pathlib
import sys

MARK = "HAKIOS-KMNOOUT"
FILE = "bsd/dev/arm/km.c"

OLD = "\t\tif (cc > 1) {\n"
NEW = ("\t\t/* " + MARK + ": ring only -- console_write() reaches the video console, and one scroll\n"
       "\t\t * there reads the framebuffer back a word at a time: seconds per line, with the writer\n"
       "\t\t * still inside the call.  The loader paints the panel from the ring anyway. */\n"
       "\t\tif (0) {\n")


def main(root):
    path = pathlib.Path(root) / FILE
    if not path.exists():
        print("patch-kernel-kmnoout: %s not found" % path)
        return 1
    text = path.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-kernel-kmnoout: already applied")
        return 0
    if text.count(OLD) != 1:
        print("patch-kernel-kmnoout: MISS: kmoutput()'s `if (cc > 1)` not found once (%d)"
              % text.count(OLD))
        return 1
    path.write_text(text.replace(OLD, NEW, 1), errors="surrogateescape")
    print("patch-kernel-kmnoout: user space writes reach the ring only; the console is not painted from km")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-kmnoout.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
