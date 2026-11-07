#!/usr/bin/env python3
"""patch-kernel-loghook.py -- connect the kernel's own log to the loader's live buffer.

The loader shows the kernel's log while it runs by reading loader_log (through the shared
kernel_live structure).  loader_log_putc is called from the CPU start, the exception tracer
and the display code -- but not from printf: kprintf's text went to the serial console only,
so the live drain showed an empty boot.

dummy_putc() is where every kprintf character passes when printf is initialised the normal
way (PE_init_kprintf installs the real routine, and dummy_putc forwards to it).  Adding one
call there puts the kernel's own text in the loader's buffer, which is what the screen drain
and 'oem logtail' read.

Anchor: the body of dummy_putc in osfmk/kern/printf.c.
"""
import sys
import pathlib

MARK = "HAKIOS-LAFA-LOGHOOK"


def patch(root):
    f = pathlib.Path(root) / "osfmk/kern/printf.c"
    if not f.exists():
        print(f"patch-loghook: {f} not found")
        return 1
    src = f.read_text(errors="surrogateescape")
    if MARK in src:
        print("patch-loghook: already applied")
        return 0

    anchor = "dummy_putc(int ch, void *arg)"
    i = src.find(anchor)
    if i < 0:
        print("patch-loghook: dummy_putc not found")
        return 1
    # the forwarding call inside it
    j = src.find("real_putc((char)ch);", i)
    if j < 0:
        print("patch-loghook: the forwarding call not found")
        return 1
    k = src.find("\n", j) + 1
    insert = (
        "\t/* HAKIOS-LAFA-LOGHOOK: the loader's live log and screen drain read loader_log;\n"
        "\t * nothing in printf wrote to it, so a running kernel showed an empty screen. */\n"
        "\tloader_log_putc((char)ch);\n"
    )
    src = src[:k] + insert + src[k:]
    # declare it ahead of first use
    decl_at = src.find("static void\ndummy_putc(int ch, void *arg)")
    if decl_at < 0:
        decl_at = i
    src = src[:decl_at] + "extern void loader_log_putc(char c);   /* HAKIOS-LAFA-LOGHOOK */\n\n" + src[decl_at:]
    f.write_text(src, errors="surrogateescape")
    print("patch-loghook: kprintf now feeds the loader's live log")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-loghook.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(patch(sys.argv[1]))
