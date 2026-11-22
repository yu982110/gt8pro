#!/usr/bin/env python3
"""HAKIOS-IMEM-MARK: a kernel heartbeat that survives the reset, so the next boot can say
whether the kernel outlived the loader.

Every run stops at 60-90 s with the kernel's ring and the loader's flash heartbeat ending in the
same second, and nothing in the guest says which of the two went first.  The flash needs the UFS
controller (the loader owns it, the kernel may too), the console cannot be trusted (kprintf blocks
on the UART, see patch-kernel-noserial.py) -- but IMEM at 0x14680000 is plain memory that the boot
chain does not clear and the guest may read and write (this loader already reads the tz-log
pointers at 0x14680720 + 0x410 every boot).

So the 5 s tick the port already has writes {magic, marker, tick, mach_absolute_time} to
0x14680FF0 (the last words of the first IMEM page, clear of the boot chain's tz-log/restart-reason
use at 0x65C..0xA20).  At the next boot the loader prints it: an absolute time at or after the
loader's own last heartbeat means the kernel outlived the loader; a time before it means the VM
went down as a whole.

Idempotent; needs patch-kernel-ticker.py applied first.

    python3 ci/patch-kernel-imem-mark.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-IMEM-MARK"

TICK_ANCHOR = "static void\nlafa_tick_dump("

FUNC = '''/* ''' + MARK + ''': a heartbeat in IMEM page 0x14680000, offset 0xFF0 -- memory that
 * survives the reset and that the next boot's loader can read (see the header of this patch).
 */
extern vm_offset_t ml_io_map_unmappable(vm_offset_t phys_addr, vm_size_t size, uint32_t flags);
#ifndef VM_WIMG_IO
#define VM_WIMG_IO 0x7
#endif

static vm_offset_t lafa_imem_va;

void
lafa_imem_mark(uint32_t what, uint64_t tick, uint64_t when)
{
\tvolatile uint32_t *m;

\tif (lafa_imem_va == 0) {
\t\tlafa_imem_va = ml_io_map_unmappable(0x14680000, 0x1000, VM_WIMG_IO);
\t}
\tif (lafa_imem_va == 0) {
\t\treturn;
\t}
\tm = (volatile uint32_t *)(lafa_imem_va + 0xFF0);
\tm[0] = 0x4B41524Du;             /* "MARK" */
\tm[1] = what;
\tm[2] = (uint32_t)tick;
\tm[3] = (uint32_t)(tick >> 32);
\tm[4] = (uint32_t)when;
\tm[5] = (uint32_t)(when >> 32);
}

'''

TICK_CALL_ANCHOR = "\tloader_log_line(line);\n\tif (++lafa_tick_round < LAFA_TICK_ROUNDS) {"
TICK_CALL = (
    "\tloader_log_line(line);\n"
    "\t/* " + MARK + ": also into IMEM, where it survives the reset */\n"
    "\tlafa_imem_mark(1, (uint64_t)lafa_tick_round + 1, mach_absolute_time());\n"
    "\tif (++lafa_tick_round < LAFA_TICK_ROUNDS) {"
)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: patch-kernel-imem-mark.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    f = next(root.rglob("bsd/kern/bsd_init.c"), None)
    if f is None:
        print("patch-imem-mark: MISS bsd/kern/bsd_init.c")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-imem-mark: already applied")
        return 0
    if TICK_ANCHOR not in text:
        print("patch-imem-mark: MISS lafa_tick_dump (run patch-kernel-ticker.py first)")
        return 1
    text = text.replace(TICK_ANCHOR, FUNC + TICK_ANCHOR, 1)
    if TICK_CALL_ANCHOR not in text:
        print("patch-imem-mark: MISS the tick body")
        return 1
    text = text.replace(TICK_CALL_ANCHOR, TICK_CALL, 1)
    f.write_text(text, encoding="utf-8")
    print("patch-imem-mark: IMEM heartbeat installed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
