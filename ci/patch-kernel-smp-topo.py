#!/usr/bin/env python3
"""HAKIOS-LAFA-TOPO: say why ml_processor_register() fails, in the boot ring.

SweetCPU reports its view (num_cpus=3, boot cpu_id=0) but all three
ml_processor_register() calls return KERN_FAILURE.  The two early exits are

    if (in_processor_info->log_id > ml_get_max_cpu_number())          return KERN_FAILURE;
    if (OSIncrementAtomic(&reg_cpu_count) >= topology_info.num_cpus)  return KERN_FAILURE;

and the ring has no way to tell which one fired, nor what the kernel's *global*
topology_info holds (it may still say 1 while ml_get_topology_info() says 3).

This patch logs, at both exits: log_id, max_cpu_number, the static counter and
topology_info.num_cpus / boot cpu id.

Idempotent; exits 0 when already applied, 1 when the anchors are gone.
"""
import pathlib
import sys

MARK = "HAKIOS-LAFA-TOPO"
DECL = 'extern "C" void loader_log_line(const char *line);\n'


def block(fmt, args):
    return (
        "\t\t{\n"
        "\t\t\tchar lafa_topo[144];\n"
        "\t\t\tsnprintf(lafa_topo, sizeof(lafa_topo),\n"
        '\t\t\t    "' + fmt + '\\n"' + args + ");\n"
        "\t\t\tloader_log_line(lafa_topo);\n"
        "\t\t}\n"
    )


EXIT1 = (
    "\tif (in_processor_info->log_id > (uint32_t)ml_get_max_cpu_number()) {\n"
)
EXIT2 = (
    "\tif ((unsigned)OSIncrementAtomic((SInt32*)&reg_cpu_count) >= topology_info.num_cpus) {\n"
)
RET = "\t\treturn KERN_FAILURE;\n"
CLOSE = "\t}\n"

B1 = block("SWEET TOPO: exit1 log_id=%u max=%d counter=%u num_cpus=%u",
           ", in_processor_info->log_id, ml_get_max_cpu_number(), reg_cpu_count,"
           " topology_info.num_cpus")
B2 = block("SWEET TOPO: exit2 log_id=%u counter=%u num_cpus=%u boot=%u",
           ", in_processor_info->log_id, reg_cpu_count, topology_info.num_cpus,"
           " (unsigned)ml_get_boot_cpu_number()")


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-smp-topo.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    f = next(root.rglob("osfmk/arm64/machine_routines.c"), None)
    if f is None:
        print("patch-topo: MISS osfmk/arm64/machine_routines.c")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-topo: already applied")
        return 0

    old1 = EXIT1 + RET + CLOSE
    old2 = EXIT2 + RET + CLOSE
    if old1 not in text:
        print("patch-topo: MISS exit 1")
        return 1
    if old2 not in text:
        # the log_id check comes first; after replacing exit 1 the file still has exit 2
        print("patch-topo: MISS exit 2")
        return 1

    text = text.replace(old1, EXIT1 + B1 + RET + CLOSE, 1)
    text = text.replace(old2, EXIT2 + B2 + RET + CLOSE, 1)
    if DECL not in text:
        i = text.find("#include")
        j = text.find("\n", i)
        text = text[:j + 1] + DECL + text[j + 1:]
    f.write_text(text, encoding="utf-8")
    print("patch-topo: both ml_processor_register exits now report to the ring")
    return 0


if __name__ == "__main__":
    sys.exit(main())
