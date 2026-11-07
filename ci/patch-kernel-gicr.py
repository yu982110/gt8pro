#!/usr/bin/env python3
"""patch-kernel-gicr.py -- give the GIC redistributor walk this SoC's stride.

The kernel died with

    Unclassified kernel abort (fault_code=0x10)

a Synchronous External Abort: a load from a device address nothing answers. Probes in
osfmk/arm/arm_init.c pinned it to PE_init_cpu(); the only MMIO on that path before bsd_init is
osfmk/arm64/gic_v3_irq.c's redistributor walk:

    for (frame = gicr_base; ...; frame += GICR_FRAME_SIZE)   <- 0x20000
            uint64_t typer = *(volatile uint64_t *)(frame + GICR_TYPER);

On SM8850 the redistributor stride is 0x40000 (lafa's own device tree: redistributor-stride =
<0x00 0x40000>), so the second iteration lands at gicr_base + 0x20000 -- a half frame that does
not exist -- and the bus answers with an abort. The first frame is fine: an 11-06 boot with
bootcpu=0 printed its gic line and reached launchd.

The fix is the stride **and nothing else**:
  * GICR_FRAME_SIZE defaults to the architectural 0x20000 (the same kernel boots QEMU virt, where
    0x20000 is right) and is taken from the device tree's "redistributor-stride" when present.
  * The TYPER scan stays: it is what picks the right frame per CPU. Deriving it from
    cpu_number() instead would be wrong -- xnu numbers CPUs by device-tree order, so with
    bootcpu=2 cpu_number() is 0 while the running core is redistributor frame 2.
  * The redistribute wake wait gets a bound: an unbounded spin there hangs with nothing logged.

    python3 ci/patch-kernel-gicr.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-GICR"

OLD_STRIDE = "#define GICR_FRAME_SIZE         0x20000     /* RD_base + SGI_base per CPU */"

NEW_STRIDE = """/*
 * HAKIOS-LAFA-GICR: the architectural stride is 0x20000 (RD_base + SGI_base), but a SoC may space
 * its redistributors further apart: SM8850 uses 0x40000 (its device tree says
 * redistributor-stride = <0x00 0x40000>), so a 0x20000 walk lands in a half frame that nothing
 * answers and the first read after that takes a Synchronous External Abort. The value is taken
 * from the device tree when it is there, and stays architectural otherwise (QEMU virt).
 */
static uint32_t gicr_frame_size = 0x20000;
#define GICR_FRAME_SIZE        gicr_frame_size"""

OLD_STRIDE_SETUP = """		uint64_t const *reg = prop;
		vm_offset_t soc_base = pe_arm_get_soc_base_phys();
"""

NEW_STRIDE_SETUP = """		uint64_t const *reg = prop;
		vm_offset_t soc_base = pe_arm_get_soc_base_phys();

		/* HAKIOS-LAFA-GICR: this SoC's redistributor stride, if the device tree says so. */
		{
			void const *stride_prop = NULL;
			unsigned int stride_size = 0;

			if (SecureDTGetProperty(entry, "redistributor-stride", &stride_prop, &stride_size) == kSuccess &&
			    stride_size == sizeof(uint32_t) && *(const uint32_t *)stride_prop >= 0x10000) {
				gicr_frame_size = *(const uint32_t *)stride_prop;
			}
			kprintf("HAKIOS gic: redistributor stride 0x%x\\n", gicr_frame_size);
		}
"""

OLD_WAKE = """		gic_write32(gicr + GICR_WAKER, waker & ~GICR_WAKER_PROCSLEEP);
		while (gic_read32(gicr + GICR_WAKER) & GICR_WAKER_CHILDASLEEP) {
			/* spin until the redistributor is awake */
		}"""

NEW_WAKE = """		gic_write32(gicr + GICR_WAKER, waker & ~GICR_WAKER_PROCSLEEP);
		for (uint64_t spins = 0; gic_read32(gicr + GICR_WAKER) & GICR_WAKER_CHILDASLEEP; spins++) {
			/* HAKIOS-LAFA-GICR: bound the wait -- an unbounded spin here hangs with no output. */
			if (spins > 2000000ULL) {
				kprintf("HAKIOS gic: redistributor 0x%llx did not wake (WAKER 0x%x); continuing\\n",
				    (unsigned long long)gicr, gic_read32(gicr + GICR_WAKER));
				break;
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
    for old, new, what in (
        (OLD_STRIDE, NEW_STRIDE, "GICR_FRAME_SIZE becomes runtime, default 0x20000"),
        (OLD_STRIDE_SETUP, NEW_STRIDE_SETUP, "stride read from the device tree"),
        (OLD_WAKE, NEW_WAKE, "redistributor wake wait is bounded"),
    ):
        if old in text:
            text = text.replace(old, new, 1)
            print("patch-gicr:", what)
        else:
            print(f"patch-gicr: MISS: {what} (anchor not found)")
            bad = 1

    # the scan stays; mark the file so a second run is a no-op
    text = text.replace(
        "\tuint32_t affinity = (uint32_t)(((mpidr >> 32) & 0xff) << 24 | (mpidr & 0xffffff));",
        "\tuint32_t affinity = (uint32_t)(((mpidr >> 32) & 0xff) << 24 | (mpidr & 0xffffff));   /* "
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
