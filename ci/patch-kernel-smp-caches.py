#!/usr/bin/env python3
"""HAKIOS-LAFA-SMPCACHE: the two osfmk/arm64/cpu.c pieces the SMP path needs.

From the reference port's 0065-sweet-smp-psci.patch, which we had not ported:

1. Before a secondary CPU is started, the reset stub reads CpuDataEntries,
   ResetHandlerData, BootArgs, sysreg_restore, cpu_ttep and the bootstrap page tables
   through physical addresses with caches OFF.  On a portable build those writes may
   still be dirty in the cache, so they have to be cleaned to PoC first -- otherwise the
   secondary core runs on stale data and dies before it can log anything (which is
   exactly what cpus=8 does now).
2. sweet_cpu_signal_is_enabled() reads the target CPU's cpu_signal; SweetCPU waits on it,
   so the stub that always returned TRUE is replaced by the real check.

Idempotent; exits 0 when already applied, 1 when the file or anchors are missing.
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-SMPCACHE"
CACHE_BLOCK = (
    "\t\t/* " + MARK + ": clean the boot data the secondary reset stub reads with caches\n"
    "\t\t * off; a dirty line here is what kills a secondary core before it can log. */\n"
    "\t\t{\n"
    "\t\t\textern uint8_t bootstrap_pagetables[BOOTSTRAP_TABLE_SIZE];\n"
    "\t\t\tCleanPoC_DcacheRegion_Force((vm_offset_t)CpuDataEntries, sizeof(CpuDataEntries));\n"
    "\t\t\tCleanPoC_DcacheRegion_Force((vm_offset_t)cpu_data_ptr, sizeof(cpu_data_t));\n"
    "\t\t\tCleanPoC_DcacheRegion_Force((vm_offset_t)&ResetHandlerData, sizeof(ResetHandlerData));\n"
    "\t\t\tCleanPoC_DcacheRegion_Force((vm_offset_t)BootArgs, sizeof(*BootArgs));\n"
    "\t\t\tCleanPoC_DcacheRegion_Force((vm_offset_t)&sysreg_restore, sizeof(sysreg_restore));\n"
    "\t\t\tCleanPoC_DcacheRegion_Force((vm_offset_t)&cpu_ttep, sizeof(cpu_ttep));\n"
    "\t\t\tCleanPoC_DcacheRegion_Force((vm_offset_t)bootstrap_pagetables, BOOTSTRAP_TABLE_SIZE);\n"
    "\t\t\t__builtin_arm_dsb(DSB_SY);\n"
    "\t\t}\n"
)
ANCHOR_CACHE = (
    "\t\tflush_dcache((vm_offset_t)&CpuDataEntries[cpu], sizeof(cpu_data_entry_t), FALSE);\n"
    "\t\tflush_dcache((vm_offset_t)cpu_data_ptr, sizeof(cpu_data_t), FALSE);\n"
)
SIGNAL_FN = (
    "\n"
    "/* " + MARK + ": the signal state SweetCPU waits on. */\n"
    "boolean_t\n"
    "sweet_cpu_signal_is_enabled(unsigned int cpu)\n"
    "{\n"
    "\tif (cpu >= MAX_CPUS) {\n"
    "\t\treturn FALSE;\n"
    "\t}\n"
    "\tcpu_data_t *cdp = CpuDataEntries[cpu].cpu_data_vaddr;\n"
    "\treturn cdp != NULL && !(os_atomic_load(&cdp->cpu_signal, acquire) & SIGPdisabled);\n"
    "}\n"
)
ANCHOR_FN = "\nvoid\ncpu_timebase_init(boolean_t from_boot)\n"


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-smp-caches.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    f = next(root.rglob("osfmk/arm64/cpu.c"), None)
    if f is None:
        print("patch-smpcache: MISS osfmk/arm64/cpu.c")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    done = 0
    if MARK in text:
        print("patch-smpcache: already applied")
        return 0
    if ANCHOR_CACHE in text:
        text = text.replace(ANCHOR_CACHE, ANCHOR_CACHE + CACHE_BLOCK, 1)
        print("patch-smpcache: cache clean block added to cpu_start")
        done += 1
    else:
        print("patch-smpcache: MISS the flush_dcache anchor")
    if ANCHOR_FN in text:
        text = text.replace(ANCHOR_FN, SIGNAL_FN + ANCHOR_FN, 1)
        print("patch-smpcache: sweet_cpu_signal_is_enabled added")
        done += 1
    else:
        print("patch-smpcache: MISS the cpu_timebase_init anchor")
    if done:
        f.write_text(text, encoding="utf-8")
    return 0 if done == 2 else 1


if __name__ == "__main__":
    sys.exit(main())
