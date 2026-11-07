#!/usr/bin/env python3
"""patch-kernel-livelog-probe.py -- probe early boot so the evidence survives a reset.

kprintf lands in the kernel's own message buffer, which only reaches the loader when the kernel
returns -- and on this hardware it does not return. loader_log_line() (hakios's own,
osfmk/arm64/loader_return.c) writes to the buffer the loader drains while the kernel runs and
persists to the boot partition every couple of seconds, so a probe written through it is on disk
even if the machine resets.

Round 1 (arm_init only) proved the whole of arm_init() runs: every probe from before
PE_init_platform(TRUE) to before machine_startup(args) was reached. This round carries the same
technique into the two functions after it, where bsd_init is called:

    osfmk/arm/arm_init.c             arm_init()            A..J
    osfmk/arm64/machine_routines.c   machine_startup()     K, L
    osfmk/kern/startup.c             kernel_bootstrap()    M..R

    python3 ci/patch-kernel-livelog-probe.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-LIVEPROBE"

DECL = 'extern void loader_log_line(const char *line);   /* ' + MARK + ' */\n'


def probe(label):
    return f'\tloader_log_line("probe: {label}\\n");\n'


# (file, anchor, where, label).  Whole-anchor replacement, one anchor each, unique in the file.
ARM_INIT = "osfmk/arm/arm_init.c"
MACHINE_ROUTINES = "osfmk/arm64/machine_routines.c"
KSTARTUP = "osfmk/kern/startup.c"

ANCHORS = [
    # ---- arm_init() ----
    (ARM_INIT, "\tPE_init_platform(TRUE, &BootCpuData);\n",
     probe("A before PE_init_platform(TRUE)"), "before"),
    (ARM_INIT, "\tPE_init_platform(TRUE, &BootCpuData);\n",
     probe("B after PE_init_platform(TRUE)"), "after"),
    (ARM_INIT, "\tml_map_cpu_pio();\n", probe("C before ml_map_cpu_pio"), "before"),
    (ARM_INIT, "\tml_map_cpu_pio();\n", probe("D after ml_map_cpu_pio"), "after"),
    (ARM_INIT, "\tcpu_timebase_init(TRUE);\n", probe("E before cpu_timebase_init"), "before"),
    (ARM_INIT, "\tcpu_timebase_init(TRUE);\n", probe("F after cpu_timebase_init"), "after"),
    # PE_init_cpu()/fiq_context_init() appear twice (boot CPU and secondary CPU paths); the boot
    # CPU pair is the one with no blank line between them, so all three probes ride on that pair.
    (ARM_INIT, "\tPE_init_cpu();\n\tfiq_context_init(TRUE);\n",
     probe("G before PE_init_cpu")
     + "\tPE_init_cpu();\n"
     + probe("H after PE_init_cpu")
     + "\tfiq_context_init(TRUE);\n"
     + probe("I after fiq_context_init"), "replace"),
    (ARM_INIT, "\tmachine_startup(args);\n", probe("J before machine_startup"), "before"),
    # ---- machine_startup() ----
    (MACHINE_ROUTINES, "machine_startup(__unused boot_args * args)\n{\n",
     probe("K machine_startup entered"), "after"),
    (MACHINE_ROUTINES, "\tmachine_conf();\n", probe("L machine_conf returned"), "after"),
    # ---- kernel_bootstrap() ----
    (KSTARTUP, "void\nkernel_bootstrap(void)\n{\n", probe("M kernel_bootstrap entered"), "after"),
    (KSTARTUP, "\tvm_mem_bootstrap();\n", probe("N vm_mem_bootstrap returned"), "after"),
    (KSTARTUP, "\tconsole_init();\n", probe("O console_init returned"), "after"),
    (KSTARTUP, "\tsched_init();\n", probe("P sched_init returned"), "after"),
    (KSTARTUP, "\tmachine_init();\n", probe("Q machine_init returned"), "after"),
    (KSTARTUP, "\tclock_init();\n", probe("R clock_init returned"), "after"),
]

# Where the declaration can go in each file: right after any include.
DECL_ANCHORS = {
    ARM_INIT: "#include <arm/pmap.h>\n",
    MACHINE_ROUTINES: "#include <arm/machine_routines.h>\n",
    KSTARTUP: "#include <kern/startup.h>\n",
}


def main(root):
    root = pathlib.Path(root)
    if (root / ARM_INIT).exists() is False:
        print(f"patch-liveprobe: {root / ARM_INIT} not found")
        return 1

    # already applied?  (the mark rides in a comment on the declaration)
    if MARK in (root / ARM_INIT).read_text(errors="surrogateescape"):
        print("patch-liveprobe: already applied")
        return 0

    by_file = {}
    for fname, anchor, repl, where in ANCHORS:
        by_file.setdefault(fname, []).append((anchor, repl, where))

    problems = 0
    placed = 0
    for fname, items in by_file.items():
        f = root / fname
        if not f.exists():
            print(f"  MISS: {fname} not found")
            problems += 1
            continue
        text = f.read_text(errors="surrogateescape")

        # the declaration, once
        anchor = DECL_ANCHORS.get(fname)
        if anchor and anchor in text:
            text = text.replace(anchor, anchor + DECL, 1)
        else:
            first = text.find("#include")
            line_end = text.find("\n", first) + 1
            text = text[:line_end] + DECL + text[line_end:]

        # the probes: every anchor of this file must be present exactly once
        for a, repl, where in items:
            first = text.find(a)
            if first < 0 or text.find(a, first + 1) >= 0:
                why = "not found" if first < 0 else "not unique"
                print(f"  MISS ({why}) in {fname}: {a.strip()!r}")
                problems += 1
                continue
            if where == "before":
                text = text[:first] + repl + text[first:]
            elif where == "replace":
                text = text[:first] + repl + text[first + len(a):]
            else:
                end = first + len(a)
                text = text[:end] + repl + text[end:]
            placed += 1

        f.write_text(text, errors="surrogateescape")

    print(f"patch-liveprobe: {placed} probes in {len(by_file)} files, {problems} problem(s)")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-livelog-probe.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
