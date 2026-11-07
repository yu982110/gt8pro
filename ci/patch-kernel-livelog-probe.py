#!/usr/bin/env python3
"""patch-kernel-livelog-probe.py -- probe early boot so the evidence survives a reset.

patch-kernel-startup-probe.py writes its probes with kprintf, which lands in the kernel's own
message buffer. That buffer is only handed to the loader when the kernel returns -- and on this
hardware the machine now resets at about 10 seconds, so the buffer (and every probe in it) is
lost with the reset.

loader_log_line() (osfmk/arm64/loader_return.c, hakios's own) writes to the buffer the loader
reads while the kernel runs and persists to the boot partition every couple of seconds. A probe
written through it is on disk before the reset, and can be read from Android afterwards.

    python3 ci/patch-kernel-livelog-probe.py <xnu tree>
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-LIVEPROBE"

DECL = ('extern void loader_log_line(const char *line);   /* ' + MARK + ' */\n')


def probe(label):
    return f'\tloader_log_line("probe: {label}\\n");\n'


# (anchor, replacement) -- whole-anchor replacements, so one insert cannot break the next.
REPLACEMENTS = [
    ("\tPE_init_platform(TRUE, &BootCpuData);\n",
     probe("A before PE_init_platform(TRUE)")
     + "\tPE_init_platform(TRUE, &BootCpuData);\n"
     + probe("B after PE_init_platform(TRUE)")),
    ("\tml_map_cpu_pio();\n",
     probe("C before ml_map_cpu_pio") + "\tml_map_cpu_pio();\n" + probe("D after ml_map_cpu_pio")),
    ("\tcpu_timebase_init(TRUE);\n",
     probe("E before cpu_timebase_init") + "\tcpu_timebase_init(TRUE);\n"
     + probe("F after cpu_timebase_init")),
    ("\tPE_init_cpu();\n\tfiq_context_init(TRUE);\n",
     probe("G before PE_init_cpu")
     + "\tPE_init_cpu();\n"
     + probe("H after PE_init_cpu")
     + "\tfiq_context_init(TRUE);\n"
     + probe("I after fiq_context_init")),
    # machine_startup() is the next thing arm_init() does; two probes bracket its start
    ("\tmachine_startup(args);\n",
     probe("J before machine_startup") + "\tmachine_startup(args);\n"),
]

DECL_ANCHOR = "#include <arm/pmap.h>\n"


def main(root):
    f = pathlib.Path(root) / "osfmk/arm/arm_init.c"
    if not f.exists():
        print(f"patch-liveprobe: {f} not found")
        return 1
    text = f.read_text(errors="surrogateescape")
    if MARK in text:
        print("patch-liveprobe: already applied")
        return 0

    if DECL_ANCHOR in text:
        text = text.replace(DECL_ANCHOR, DECL_ANCHOR + DECL, 1)
        print("patch-liveprobe: declaration added")
    else:
        # fall back to just after the first include block
        first = text.find("#include")
        line_end = text.find("\n", first) + 1
        text = text[:line_end] + DECL + text[line_end:]
        print("patch-liveprobe: declaration added (fallback anchor)")

    problems = 0
    placed = 0
    for anchor, replacement in REPLACEMENTS:
        first = text.find(anchor)
        if first < 0:
            print(f"  MISS: {anchor.strip()!r}")
            problems += 1
            continue
        if text.find(anchor, first + 1) >= 0:
            print(f"  MISS (not unique): {anchor.strip()!r}")
            problems += 1
            continue
        text = text[:first] + replacement + text[first + len(anchor):]
        placed += 1

    f.write_text(text, errors="surrogateescape")
    print(f"patch-liveprobe: {placed}/{len(REPLACEMENTS)} anchors, {problems} problem(s)")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: patch-kernel-livelog-probe.py <xnu tree>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
