#!/usr/bin/env python3
"""patch-kernel-startup-probe.py -- name the step that follows PE_init_platform(TRUE).

The kernel on lafa prints its whole early log and then dies with
    Unclassified kernel abort (fault_code=0x10)
which is a Synchronous External Abort: a load or store to a device address that nothing answers.
The last line before it is pe_arm_init_debug's warning, so the fault is in one of the calls that
follow in osfmk/arm/arm_init.c:

    PE_init_platform(TRUE, &BootCpuData);
    ml_map_cpu_pio();
    cpu_timebase_init(TRUE);
    PE_init_cpu();
    fiq_context_init(TRUE);

Each is bracketed here with a kprintf that names it, so one boot says which call is the last one
reached -- and therefore which one takes the abort. (kprintf reaches the loader: it dumps the
kernel message buffer when the kernel comes back.)

Every step is a whole-anchor replacement, so inserting one probe cannot break the anchor of the
next. Anchors are unique in the vanilla file; the boot-CPU PE_init_cpu()/fiq_context_init() pair
is the one with no blank line between the two calls (the secondary-CPU path has one).

    python3 ci/patch-kernel-startup-probe.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-PROBE"


def probe(label):
    return f'\tkprintf("probe: {label}\\n");\n'


REPLACEMENTS = [
    # 1) around PE_init_platform(TRUE, ...)
    ("\tPE_init_platform(TRUE, &BootCpuData);\n",
     probe("A before PE_init_platform(TRUE)")
     + "\tPE_init_platform(TRUE, &BootCpuData);\n"
     + probe("B after PE_init_platform(TRUE)")),
    # 2) around ml_map_cpu_pio()
    ("\tml_map_cpu_pio();\n",
     probe("C before ml_map_cpu_pio") + "\tml_map_cpu_pio();\n" + probe("D after ml_map_cpu_pio")),
    # 3) around cpu_timebase_init()
    ("\tcpu_timebase_init(TRUE);\n",
     probe("E before cpu_timebase_init") + "\tcpu_timebase_init(TRUE);\n"
     + probe("F after cpu_timebase_init")),
    # 4) the boot-CPU pair, all three probes in one replacement
    ("\tPE_init_cpu();\n\tfiq_context_init(TRUE);\n",
     probe("G before PE_init_cpu")
     + "\tPE_init_cpu();\n"
     + probe("H after PE_init_cpu")
     + "\tfiq_context_init(TRUE);\n"
     + probe("I after fiq_context_init")),
]


def main(root):
    f = pathlib.Path(root) / "osfmk/arm/arm_init.c"
    if not f.exists():
        print(f"patch-startup-probe: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-startup-probe: already applied")
        return 0

    problems = 0
    for anchor, replacement in REPLACEMENTS:
        first = text.find(anchor)
        if first < 0:
            print(f"  MISS: anchor not found: {anchor.strip()!r}")
            problems += 1
            continue
        if text.find(anchor, first + 1) >= 0:
            print(f"  MISS: anchor not unique: {anchor.strip()!r}")
            problems += 1
            continue
        text = text[:first] + replacement + text[first + len(anchor):]

    # one marker in a comment, so a second run is a no-op
    text = text.replace(
        'kprintf("probe: A before PE_init_platform(TRUE)\\n");',
        'kprintf("probe: A before PE_init_platform(TRUE)\\n");   /* ' + MARK + ' */',
        1,
    )
    f.write_text(text, errors="surrogateescape")
    total = sum(len(r.split("probe:")) - 1 for _, r in REPLACEMENTS)
    print(f"patch-startup-probe: {total} probes inserted, {problems} problem(s)")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-startup-probe.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
