#!/usr/bin/env python3
"""HAKIOS-LAFA-SMP: let the kernel bring up all eight Oryon cores.

The boot log said it plainly:

    HAKIOS-CPU: logical_cpu_max 1

SM8850 has six cores at MPIDR Aff1 = 0..5 and two prime cores at Aff2 = 1
(0x10000, 0x10100).  The kernel masks MPIDR down to Aff1:Aff0 in two places, so it
sees the first core and nothing else -- and a one-CPU kernel deadlocks any
two-thread handshake, which is exactly the backboardd ulock_wait stall.

Reference: refs/data-a-live/xnu/patches/0113-pandora-smp-aff2-prime-cores.patch
(the same SoC).  This script makes the same three edits plus the CPU-count clamp:

  1. osfmk/arm64/start.s            -- MPIDR mask 0xFFFF  -> 0xFFFFFF
  2. osfmk/arm64/machine_routines.c -- ml_get_cpu_number(): add MPIDR_AFF2_MASK
  3. pexpert/arm/pe_fiq.c           -- find_gicr_pe_base(): compare three affinities
  4. pexpert/pexpert/arm64/*.h      -- SWEET_SMP_MAX_CPUS: allow 8

Idempotent; exits 0 when already applied, 1 when nothing matched.
"""
import pathlib
import re
import sys

MARK = "HAKIOS-LAFA-SMP"
done = 0


def edit(path, pattern, repl, what):
    global done
    if not path.exists():
        print(f"patch-smp: MISS {path} ({what})")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    if repl.strip() and repl.strip() in text:
        print(f"patch-smp: already applied: {what}")
        done += 1
        return
    new, n = re.subn(pattern, repl, text, count=1)
    if n == 0:
        print(f"patch-smp: MISS anchor: {what}")
        return
    path.write_text(new, encoding="utf-8")
    print(f"patch-smp: {what}")
    done += 1


def main():
    global done
    if len(sys.argv) < 2:
        print("usage: patch-kernel-smp-aff2.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])

    # 1) reset vector: keep all three affinity levels when indexing cpu_data
    start_s = next(root.rglob("osfmk/arm64/start.s"), None)
    if start_s:
        edit(start_s,
             r"and[ \t]+x0,[ \t]*x15,[ \t]*#0xFFFF\b",
             "and\t\tx0, x15, #0xFFFFFF\t\t\t\t\t/* " + MARK + ": Aff2:Aff1:Aff0 */",
             "start.s MPIDR mask 0xFFFFFF")

    # 2) cpu number lookup: include Aff2 (the two prime cores differ only there)
    ml = next(root.rglob("osfmk/arm64/machine_routines.c"), None)
    if ml:
        edit(ml,
             r"phys_id[ \t]*&=[ \t]*MPIDR_AFF1_MASK[ \t]*\|[ \t]*MPIDR_AFF0_MASK;",
             "phys_id &= MPIDR_AFF2_MASK | MPIDR_AFF1_MASK | MPIDR_AFF0_MASK; /* " + MARK + " */",
             "machine_routines.c ml_get_cpu_number Aff2")

    # 3) GIC redistributor lookup: compare Aff2 as well
    pf = next(root.rglob("pexpert/arm/pe_fiq.c"), None)
    if pf:
        edit(pf,
             r'__builtin_arm_rsr64\("MPIDR_EL1"\)[ \t]*&[ \t]*\(MPIDR_AFF1_MASK[ \t]*\|[ \t]*MPIDR_AFF0_MASK\)',
             '__builtin_arm_rsr64("MPIDR_EL1") & (MPIDR_AFF2_MASK | MPIDR_AFF1_MASK | MPIDR_AFF0_MASK)',
             "pe_fiq.c physical id mask")
        edit(pf,
             r"\(gicr_typer[ \t]*>>[ \t]*GICR_TYPER_AFFINITY_VALUE_SHIFT\)[ \t]*&[ \t]*\(MPIDR_AFF1_MASK[ \t]*\|[ \t]*MPIDR_AFF0_MASK\)",
             "(gicr_typer >> GICR_TYPER_AFFINITY_VALUE_SHIFT) & (MPIDR_AFF2_MASK | MPIDR_AFF1_MASK | MPIDR_AFF0_MASK)",
             "pe_fiq.c GICR affinity compare")

    # 4) CPU count clamp
    for h in root.rglob("pexpert/pexpert/arm64/*.h"):
        text = h.read_text(encoding="utf-8", errors="replace")
        if "SWEET_SMP_MAX_CPUS" not in text:
            continue
        new, n = re.subn(r"#define SWEET_SMP_MAX_CPUS[ \t]+\d+",
                         "#define SWEET_SMP_MAX_CPUS         8 /* " + MARK + " */",
                         text, count=1)
        if n:
            h.write_text(new, encoding="utf-8")
            print(f"patch-smp: {h.name}: SWEET_SMP_MAX_CPUS = 8")
            done += 1
        break

    return 0 if done >= 3 else 1


if __name__ == "__main__":
    sys.exit(main())
