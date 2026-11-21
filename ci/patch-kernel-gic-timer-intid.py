#!/usr/bin/env python3
"""Use the timer interrupt number this SoC actually wires, and keep the architectural one too.

A working SM8850 port records the difference in as many words: the kernel's timer interrupt
is PPI 19 on Qualcomm, not the architectural PPI 27 that this file enables.  This kernel
reaches grade_binary and then stops dead -- no output, the heartbeat timer never fires, and
`oem stop` cannot reach it -- which is exactly what a kernel looks like when its timer
interrupt is enabled on an ID the hardware never raises: rtclock_intr() is never called, so
no thread_call (and nothing else with a timeout) ever runs again.

Both IDs are enabled and both are treated as the timer: if this SoC does raise 27 after all,
answering it costs nothing, and enabling an unrouted PPI is harmless.

usage: patch-kernel-gic-timer-intid.py <xnu source dir>
"""
import pathlib
import sys

MARK = "HAKIOS-GIC-TIMER-INTID"

OLD_DEFINE = "#define GIC_INTID_VTIMER        27          /* EL1 virtual timer PPI */\n"
NEW_DEFINE = """#define GIC_INTID_VTIMER        19          /* HAKIOS-GIC-TIMER-INTID: the EL1 virtual timer is
                                             * PPI 19 on this SoC, not the architectural 27 -- a
                                             * working SM8850 port records exactly that ("Qualcomm's
                                             * timer interrupt number is not the standard one: the
                                             * virtual timer is 19, not 27"). With 27 the timer
                                             * interrupt was enabled on an ID the hardware never
                                             * raises: rtclock_intr() never ran, no thread_call
                                             * ever fired again, and the kernel stopped silently. */
#define GIC_INTID_VTIMER_ARCH   27          /* enabled too, and answered if it ever comes */
"""

OLD_MINE = "\tuint32_t mine = 1u << GIC_INTID_VTIMER;\n"
NEW_MINE = "\tuint32_t mine = (1u << GIC_INTID_VTIMER) | (1u << GIC_INTID_VTIMER_ARCH);\n"

OLD_HANDLE = "\t\tif (intid == GIC_INTID_VTIMER) {\n"
NEW_HANDLE = "\t\tif (intid == GIC_INTID_VTIMER || intid == GIC_INTID_VTIMER_ARCH) {\n"

OLD_PRIO = "\t*(volatile uint8_t *)(gicr + GICR_IPRIORITYR(GIC_INTID_VTIMER)) = GIC_PRIORITY_DEFAULT;\n"
NEW_PRIO = ("\t*(volatile uint8_t *)(gicr + GICR_IPRIORITYR(GIC_INTID_VTIMER)) = GIC_PRIORITY_DEFAULT;\n"
            "\t*(volatile uint8_t *)(gicr + GICR_IPRIORITYR(GIC_INTID_VTIMER_ARCH)) = GIC_PRIORITY_DEFAULT;\n")


def main(root):
    f = pathlib.Path(root) / "osfmk/arm64/gic_v3_irq.c"
    if not f.exists():
        print("patch-gic-timer-intid: osfmk/arm64/gic_v3_irq.c not found")
        return 1
    t = f.read_text(errors="surrogateescape")
    if MARK in t:
        print("patch-gic-timer-intid: already there")
        return 0
    rc = 0
    for old, new, name in ((OLD_DEFINE, NEW_DEFINE, "GIC_INTID_VTIMER 19"),
                           (OLD_MINE, NEW_MINE, "enable both PPIs"),
                           (OLD_HANDLE, NEW_HANDLE, "answer both PPIs"),
                           (OLD_PRIO, NEW_PRIO, "priority on both")):
        if old not in t:
            print("patch-gic-timer-intid: MISS anchor: %s" % name)
            rc = 1
            continue
        t = t.replace(old, new, 1)
        print("patch-gic-timer-intid: %s" % name)
    f.write_text(t, errors="surrogateescape")
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
