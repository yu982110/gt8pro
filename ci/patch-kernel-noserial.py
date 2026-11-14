#!/usr/bin/env python3
"""patch-kernel-noserial.py -- keep kprintf off the UART, so a stuck transmitter cannot stop the boot.

The boot stops at ~58 s with the kernel alive (HAKIOS-TICK keeps arriving through loader_log_line)
and every kprintf afterwards silent.  The last thing every kprintf does is

    pexpert/arm/pe_kprintf.c:  serial_putc_crlf() -> uart_putc(c)

and this phone's UART has nothing reading it.  A transmitter whose FIFO is full and never drains
spins there, and it does so inside kprintf's lock: from then on every print blocks (the port's own
ring writer, loader_log_line, does not use that path, which is why the tick keeps coming).

So: hold the UART quiet and see whether the boot walks past the point where it used to stop.  If it
does, the transmitter is confirmed and the fix is to make serial output conditional (or time its
writes out) instead of unconditional.

    python3 ci/patch-kernel-noserial.py <xnu tree>

Idempotent (MARK). Anchors are the post-hakios.patch text of serial_putc_crlf(), i.e. run this after
xnu/apply.sh, which is where the workflows run their patches.
"""
import pathlib
import sys

MARK = "HAKIOS-NOSERIAL"
FILE = "pexpert/arm/pe_kprintf.c"

OLD = (
    "static void\n"
    "serial_putc_crlf(char c)\n"
    "{\n"
    "\tif (c == '\\n') {\n"
    "\t\tuart_putc('\\r');\n"
    "\t}\n"
    "\tloader_log_putc(c);\n"
    "\tuart_putc(c);\n"
    "}\n"
)

NEW = (
    "static void\n"
    "serial_putc_crlf(char c)\n"
    "{\n"
    "\t/* HAKIOS-NOSERIAL: the port's ring is the log; the UART has nothing reading it, and a full\n"
    "\t * FIFO spins here inside kprintf's lock -- every later print then blocks for good. */\n"
    "\tloader_log_putc(c);\n"
    "}\n"
)


def main(root):
    path = pathlib.Path(root) / FILE
    if not path.exists():
        print("patch-kernel-noserial: %s not found" % path)
        return 1
    text = path.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-kernel-noserial: already applied")
        return 0
    if text.count(OLD) == 1:
        path.write_text(text.replace(OLD, NEW, 1), errors="surrogateescape")
        print("patch-kernel-noserial: kprintf writes the ring only; the UART is left alone")
        return 0
    # Fallback for a tree whose whitespace differs: take the two calls out where they are.
    tail = "\tloader_log_putc(c);\n\tuart_putc(c);\n"
    cr = "\t\tuart_putc('\\r');\n"
    if text.count(tail) == 1 and text.count(cr) == 1:
        text = text.replace(cr, "", 1)
        text = text.replace(tail, "\t/* " + MARK + " */\n\tloader_log_putc(c);\n", 1)
        path.write_text(text, errors="surrogateescape")
        print("patch-kernel-noserial: kprintf writes the ring only (fallback edit); the UART is left alone")
        return 0
    print("patch-kernel-noserial: MISS: serial_putc_crlf() not found in either form; is hakios.patch applied?")
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-noserial.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
