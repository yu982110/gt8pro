#!/usr/bin/env python3
"""Make serial_init() return straight away, so the kernel can get past it and set PE_kputc.

The chain of facts that puts the boot stop here:

  * the loader reads PE_kputc (the pointer kprintf calls) at runtime: it is 0;
  * the loader dumps the kernel's log ring: all zero, so nothing was ever printed;
  * no loader_checkpoint() in arm_init fires, and every one of them is after L531;
  * arm_init.c does, in order (Apple xnu-8796 osfmk/arm/arm_init.c L526-531):

        PE_consistent_debug_inherit();
        const unsigned int serial_exists = serial_init();          /* L529 */
        kernel_startup_initialize_upto(STARTUP_SUB_KPRINTF);        /* PE_init_kprintf is in here */
        kprintf("kprintf initialized\\n");

    and PE_kputc is set inside PE_init_kprintf.  It is 0, so nothing at or after L530 ran,
    so the stop is one of L526-L529, and serial_init() is the only call there that touches
    hardware this phone may not have.

This is the one variable this round: serial_init() returns 0 at the top.  Nothing else about
the kernel changes; the boot-stage marks are observability only (one word in the live struct
the loader already reads) and do not alter any behaviour.

Difference from upstream: the SM8850 port that boots iOS 16 keeps serial_init() as it is and
takes the UART out of serial_putc_crlf() only (xnu/patches/0012/0047/…).  That leaves the
probe in arm_init untouched; this port short-circuits the probe itself, because on this phone
PE_kputc proves the kernel never got past it.

usage: patch-kernel-noserial-init.py <xnu source dir>
"""
import pathlib
import sys

MARK = "HAKIOS-NOSERIAL-INIT"

OLD = "int\nserial_init(void)\n{\n"
NEW = """int
serial_init(void)
{
\t/*
\t * HAKIOS-NOSERIAL-INIT: there is no UART here for the kernel to reach, and PE_kputc is
\t * still 0 when the loader looks at it -- so this call, the last thing before kprintf is
\t * set up in arm_init, never came back.  Answer "no serial" and let the rest of arm_init
\t * run; kprintf then uses serial_putc_crlf, which patch-kernel-noserial.py has already
\t * emptied of UART writes, so it feeds pexpert's loader_log_putc ring and nothing else.
\t */
\treturn 0;

"""


def main(root):
    p = pathlib.Path(root) / "pexpert/arm/pe_serial.c"
    if not p.exists():
        print("patch-noserial-init: %s not found" % p)
        return 1
    t = p.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-noserial-init: already applied")
        return 0
    if t.count(OLD) != 1:
        print("patch-noserial-init: MISS serial_init() (found %d)" % t.count(OLD))
        return 1
    p.write_text(t.replace(OLD, NEW, 1), errors="surrogateescape")
    print("patch-noserial-init: serial_init() returns 0 at once")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
