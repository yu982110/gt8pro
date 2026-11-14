#!/usr/bin/env python3
"""patch-kernel-vcnoop.py -- make the video console's putc a no-op, so console writes always return.

The console write path is:

    user space write(/dev/console) -> tty -> kmoutput() -> console_write()
                                                             -> _cnputc() -> cons_ops[idx].putc()

and for the video console that last call is vcputc_options(), which draws the character and, when the
cursor is at the bottom of the screen, scrolls -- and the port's own note in xnu/hakios.patch says a
scroll reads the framebuffer back "one bus access per word -- seconds per line on a phone-sized
screen".  The writer sits inside that call: the process that printed the last line on screen never
returns, and the boot stops there.

Dropping console_write() altogether is not the answer either: the tty's output queue then has no
drain, fills up, and the next write sleeps in ttysleep instead.  What is needed is a putc that
returns immediately while the queue still drains -- this.

Nothing is lost: the panel is the loader's (it drains the ring and repaints), and the ring itself is
fed from kmoutput() and from PE_kputc, not from here.

    python3 ci/patch-kernel-vcnoop.py <xnu tree>

Idempotent (MARK); the guard becomes `if (0)` so every brace and the rest of the function stay put.
"""
import pathlib
import sys

MARK = "HAKIOS-VCNOOP"
FILE = "osfmk/console/video_console.c"

OLD = "\tif (gc_initialized && gc_enabled) {\n"
NEW = ("\t/* " + MARK + ": draw nothing here.  The ring is written elsewhere and the loader paints\n"
       "\t * the panel from it; drawing in this path means scrolling inside the caller's write, which\n"
       "\t * on this panel takes seconds per line. */\n"
       "\tif (0) {\n")


def main(root):
    path = pathlib.Path(root) / FILE
    if not path.exists():
        print("patch-kernel-vcnoop: %s not found" % path)
        return 1
    text = path.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-kernel-vcnoop: already applied")
        return 0
    if text.count(OLD) != 1:
        print("patch-kernel-vcnoop: MISS: vcputc_options()'s guard found %d times" % text.count(OLD))
        return 1
    path.write_text(text.replace(OLD, NEW, 1), errors="surrogateescape")
    print("patch-kernel-vcnoop: the video console draws nothing; console writes return at once")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-vcnoop.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
