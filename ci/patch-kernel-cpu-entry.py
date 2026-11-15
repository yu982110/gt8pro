#!/usr/bin/env python3
"""HAKIOS-LAFA-CPUS: append the PSCI secondary-CPU entry to osfmk/arm64/start.s.

SweetCPU.cpp starts the other cores with PSCI CPU_ON, which lands in
sweet_secondary_entry() -- a small assembly stub the reference port adds to start.s
(0065-sweet-smp-psci.patch).  Without it the kernel does not link.

The reference guards the block with #if defined(SWEET); our board is lafa, so the block
is emitted unconditionally here.

Idempotent; exits 0 when already applied, 1 when start.s is missing.
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-CPUS"
BLOCK = r'''
/* %MARK%: PSCI CPU_ON entry (physical): x0=context_id, no stack, MMU/caches off.
 * Keep this separate from __start (which requires x0=boot_args PA).
 * Firmware may return at EL1 or EL2; check before accessing EL1 state.
 */
	.align 2
	.globl EXT(sweet_secondary_entry)
LEXT(sweet_secondary_entry)
	msr		DAIFSet, #0xf
	mrs		x1, CurrentEL
	cmp		x1, #0x4
	b.eq	Lsweet_secondary_el1
	cmp		x1, #0x8
	b.ne	Lsweet_secondary_bad_el
	adrp	x1, EXT(LowExceptionVectorBase)@page
	add		x1, x1, EXT(LowExceptionVectorBase)@pageoff
	msr		VBAR_EL2, x1
	/* No stage 2, AArch64 EL1, no EL2 interrupt or FP/debug traps. */
	mov		x1, #(1 << 31)
	msr		HCR_EL2, x1
	msr		VTTBR_EL2, xzr
	msr		CPTR_EL2, xzr
	msr		MDCR_EL2, xzr
	mov		x1, #3
	msr		CNTHCTL_EL2, x1
	msr		CNTVOFF_EL2, xzr
	/* Permit ICC_SRE_EL1 and GIC CPU interface system-register access. */
	mrs		x1, ICC_SRE_EL2
	orr		x1, x1, #1
	orr		x1, x1, #8
	msr		ICC_SRE_EL2, x1
	/* Known Armv8 SCTLR RES1 bits, M/C/I off, little endian. */
	MOV64	x1, 0x30d00800
	msr		SCTLR_EL1, x1
	isb
	adrp	x1, Lsweet_secondary_el1@page
	add		x1, x1, Lsweet_secondary_el1@pageoff
	msr		ELR_EL2, x1
	mov		x1, #0x3c5			// DAIF masked, AArch64 EL1h
	msr		SPSR_EL2, x1
	eret
Lsweet_secondary_bad_el:
	b		.
Lsweet_secondary_el1:
	/* x0 is deliberately unused; reset_vector selects CPU data by MPIDR. */
	msr		SPSel, #1
	b		EXT(LowResetVectorBase)
'''.replace("%MARK%", MARK)


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-cpu-entry.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    f = next(root.rglob("osfmk/arm64/start.s"), None)
    if f is None:
        print("patch-cpus: MISS osfmk/arm64/start.s")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-cpus: sweet_secondary_entry already in start.s")
        return 0
    # insert just before the LowResetVectorBase / reset vector area end: append at EOF is
    # fine for an assembly file as long as it stays inside a recognised section; the
    # reference puts it after bootstrap_instructions, so mirror that by appending.
    if not text.endswith("\n"):
        text += "\n"
    text += BLOCK
    f.write_text(text, encoding="utf-8")
    print("patch-cpus: sweet_secondary_entry appended to start.s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
