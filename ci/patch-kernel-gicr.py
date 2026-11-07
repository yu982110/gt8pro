#!/usr/bin/env python3
"""patch-kernel-gicr.py -- fix the GIC redistributor walk that aborts on lafa.

The kernel died with

    Unclassified kernel abort (fault_code=0x10)

which is a Synchronous External Abort -- a load from a device address nothing answers.  Probes in
osfmk/arm/arm_init.c pinned it to PE_init_cpu(); the only MMIO that path touches before bsd_init
is osfmk/arm64/gic_v3_irq.c's redistributor walk:

    for (frame = gicr_base; ...; frame += GICR_FRAME_SIZE)
            uint64_t typer = *(volatile uint64_t *)(frame + GICR_TYPER);   <- first read faults

Two things are wrong for this SoC (SM8850), both confirmed by the same-SoC reference project:

  1. GICR_FRAME_SIZE is 0x20000 (the architectural RD_base+SGI_base size); on this SoC the
     redistributor stride measured on the real device is 0x40000 (RD_base + SGI_base + 64 KiB
     unused + 64 KiB VLPI frame). Walking at 0x20000 lands in the middle of a frame.
  2. The walk starts by reading GICR_TYPER, and that very read is what takes the external abort.

The fix does both: the stride becomes 0x40000, and the boot CPU's frame is derived from
cpu_number() instead of probed, so nothing is read from the redistributors before the exception
machinery exists.

    python3 ci/patch-kernel-gicr.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-GICR"

OLD_STRIDE = "#define GICR_FRAME_SIZE         0x20000     /* RD_base + SGI_base per CPU */"

NEW_STRIDE = """/*
 * HAKIOS-LAFA-GICR: this SoC's redistributor stride, not the architectural 0x20000. The same-SoC
 * reference measured it on the real device: redistributor base 0x17080000, stride 0x40000
 * (RD_base + SGI_base + 64 KiB unused + 64 KiB VLPI frame). A walk at 0x20000 lands mid-frame and
 * the bus answers with a Synchronous External Abort.
 */
#define GICR_FRAME_SIZE         0x40000"""

OLD_LOOP = """	for (vm_offset_t frame = gicr_base; frame + GICR_FRAME_SIZE <= gicr_base + gicr_size; frame += GICR_FRAME_SIZE) {
		uint64_t typer = *(volatile uint64_t *)(frame + GICR_TYPER);

		if ((uint32_t)(typer >> 32) == affinity) {
			return frame;
		}
		if (typer & GICR_TYPER_LAST) {
			break;
		}
	}"""

# The redistributor wake wait spins without a bound; on this hardware CHILDASLEEP can stay set
# (the frame reads back as the same value it was written), which hangs the kernel with no output.
OLD_WAKE = """		gic_write32(gicr + GICR_WAKER, waker & ~GICR_WAKER_PROCSLEEP);
		while (gic_read32(gicr + GICR_WAKER) & GICR_WAKER_CHILDASLEEP) {
			/* spin until the redistributor is awake */
		}"""

NEW_WAKE = """		gic_write32(gicr + GICR_WAKER, waker & ~GICR_WAKER_PROCSLEEP);
		for (uint64_t spins = 0; gic_read32(gicr + GICR_WAKER) & GICR_WAKER_CHILDASLEEP; spins++) {
			/* HAKIOS-LAFA-GICR: bound the wait. On this hardware the frame can read back
			 * unchanged, and an unbounded spin here hangs the kernel with nothing logged. */
			if (spins > 2000000ULL) {
				kprintf("HAKIOS gic: redistributor 0x%llx did not wake (WAKER 0x%x); continuing\\n",
				    (unsigned long long)gicr, gic_read32(gicr + GICR_WAKER));
				break;
			}
		}"""

NEW_LOOP = """	for (vm_offset_t frame = gicr_base; frame + GICR_FRAME_SIZE <= gicr_base + gicr_size; frame += GICR_FRAME_SIZE) {
		/*
		 * HAKIOS-LAFA-GICR: the boot CPU's frame is derived, not read. On this SoC the first
		 * TYPER read is itself what took the Synchronous External Abort, so the walk must not
		 * start with one. Redistributors are one frame per CPU, in CPU order.
		 */
		if (frame == gicr_base + (vm_offset_t)cpu_number() * GICR_FRAME_SIZE) {
			return frame;
		}
	}"""


def main(root):
    f = pathlib.Path(root) / "osfmk/arm64/gic_v3_irq.c"
    if not f.exists():
        print(f"patch-gicr: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-gicr: already applied")
        return 0

    bad = 0
    if OLD_STRIDE in text:
        text = text.replace(OLD_STRIDE, NEW_STRIDE, 1)
        print("patch-gicr: GICR_FRAME_SIZE 0x20000 -> 0x40000")
    else:
        print("patch-gicr: MISS: the GICR_FRAME_SIZE line is not the expected one")
        bad = 1

    if OLD_LOOP in text:
        text = text.replace(OLD_LOOP, NEW_LOOP, 1)
        print("patch-gicr: no TYPER read before the redistributor is known")
    else:
        print("patch-gicr: MISS: the redistributor walk is not the expected one")
        bad = 1

    if OLD_WAKE in text:
        text = text.replace(OLD_WAKE, NEW_WAKE, 1)
        print("patch-gicr: redistributor wake wait is bounded")
    else:
        print("patch-gicr: MISS: the redistributor wake wait is not the expected one")
        bad = 1

    # keep the affinity value used (it appears in the panic message)
    text = text.replace(
        "\tuint32_t affinity = (uint32_t)(((mpidr >> 32) & 0xff) << 24 | (mpidr & 0xffffff));",
        "\tuint32_t affinity __unused = (uint32_t)(((mpidr >> 32) & 0xff) << 24 | (mpidr & 0xffffff));   /* "
        + MARK + " */",
        1,
    )
    f.write_text(text, errors="surrogateescape")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-gicr.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
