#!/usr/bin/env python3
"""patch-kernel-novideocon.py -- keep kprintf off the video console.

PE_init_kprintf() picks the character sink for every kernel print:

    if (serial_init()) {
            PE_kputc = serial_putc_crlf;      /* UART + this port's ring hook */
    } else {
            PE_kputc = console_write_unbuffered;  /* the video console */
    }

On this phone the video console is a trap: when the screen fills up and the console scrolls, it
scrolls by reading the framebuffer back line by line, and the port's own note (xnu/hakios.patch,
osfmk/console/video_console.c) says that as device memory is "one bus access per word - seconds per
line on a phone-sized screen".  Every kprintf holds interrupts off while it writes characters, so a
scroll in there stops the whole machine for as long as it takes -- which is exactly what the boot
shows: everything goes quiet at the moment the screen is full, the kernel keeps ticking through
loader_log_line (a path that does not use the console), and no I/O follows.

The panel does not need the kernel: the loader paints it from the ring (live_log_drain).  So send
every character to serial_putc_crlf, whose ring hook is the port's own path -- and pair this with
patch-kernel-noserial.py, which takes the UART out of that function, leaving the ring alone.

    python3 ci/patch-kernel-novideocon.py <xnu tree>

Idempotent (MARK). Run after xnu/apply.sh, like the other ci patches.
"""
import pathlib
import sys

MARK = "HAKIOS-NOVIDEOCON"
FILE = "pexpert/arm/pe_kprintf.c"

OLD = (
    "\tif (serial_init()) {\n"
    "\t\tPE_kputc = serial_putc_crlf;\n"
    "\t} else {\n"
    "\t\t/**\n"
    "\t\t * If serial failed to initialize then fall back to using the console,\n"
    "\t\t * and assume the console is using the video console (because clearly\n"
    "\t\t * serial doesn't work).\n"
    "\t\t */\n"
    "\t\tPE_kputc = console_write_unbuffered;\n"
    "\t}\n"
)

NEW = (
    "\t/* HAKIOS-NOVIDEOCON: never hand kernel prints to the video console -- its scroll reads the\n"
    "\t * framebuffer back a word at a time, and every kprintf does this with interrupts off.  The\n"
    "\t * panel is the loader's business (it drains the ring); serial_putc_crlf carries the ring\n"
    "\t * hook, and patch-kernel-noserial.py takes the UART out of it. */\n"
    "\tPE_kputc = serial_putc_crlf;\n"
)


def main(root):
    path = pathlib.Path(root) / FILE
    if not path.exists():
        print("patch-kernel-novideocon: %s not found" % path)
        return 1
    text = path.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-kernel-novideocon: already applied")
        return 0
    if text.count(OLD) != 1:
        print("patch-kernel-novideocon: MISS: PE_init_kprintf()'s sink choice not found once (%d)"
              % text.count(OLD))
        return 1
    path.write_text(text.replace(OLD, NEW, 1), errors="surrogateescape")
    print("patch-kernel-novideocon: PE_kputc is serial_putc_crlf always; the video console is out of the path")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-novideocon.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
