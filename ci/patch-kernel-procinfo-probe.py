#!/usr/bin/env python3
"""HAKIOS-LAFA-PROCI: say where proc_info() hangs.

The freeze is now bracketed to the last syscall launchd makes after logd dies:

    HAKIOS-SYS launchd.darwin 37 kill
    HAKIOS-SYS launchd.darwin 336 proc_info      <- last line, then the kernel stops

proc_info() is a thin wrapper around proc_info_internal(), so a line at entry (pid, call,
flavor) and a line at exit (the return value) is enough: if the entry line is the last
thing in the ring, the hang is inside that flavour's handler and its argument names it.

    python3 ci/patch-kernel-procinfo-probe.py <xnu tree>

Idempotent; exits 0 when already applied, 1 when the wrapper text is not found.
"""
import pathlib
import sys

MARK = "HAKIOS-PROCI"

OLD = (
    "\treturn proc_info_internal(uap->callnum, uap->pid, 0, 0, uap->flavor, uap->arg, uap->buffer, uap->buffersize, retval);\n"
)
NEW = (
    "\t/* " + MARK + ": the last syscall before the freeze; entry and result name the flavour. */\n"
    "\t{\n"
    "\t\tchar lafa_l[128];\n"
    "\n"
    "\t\tsnprintf(lafa_l, sizeof(lafa_l),\n"
    "\t\t    \"HAKIOS-PROCI entry pid %d call %d flavor %d\\n\",\n"
    "\t\t    uap->pid, (int)uap->callnum, (int)uap->flavor);\n"
    "\t\tloader_log_line(lafa_l);\n"
    "\t}\n"
    "\n"
    "\tint lafa_ret = proc_info_internal(uap->callnum, uap->pid, 0, 0, uap->flavor, uap->arg,\n"
    "\t    uap->buffer, uap->buffersize, retval);\n"
    "\n"
    "\t{\n"
    "\t\tchar lafa_l[128];\n"
    "\n"
    "\t\tsnprintf(lafa_l, sizeof(lafa_l), \"HAKIOS-PROCI done pid %d flavor %d -> %d\\n\",\n"
    "\t\t    uap->pid, (int)uap->flavor, lafa_ret);\n"
    "\t\tloader_log_line(lafa_l);\n"
    "\t}\n"
    "\treturn lafa_ret;\n"
)
DECL = "extern void loader_log_line(const char *line);   /* " + MARK + " */\n"


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kernel-procinfo-probe.py <xnu tree>")
        return 2
    root = pathlib.Path(sys.argv[1])
    f = next(root.rglob("bsd/kern/proc_info.c"), None)
    if f is None:
        print("patch-proci: MISS bsd/kern/proc_info.c")
        return 1
    text = f.read_text(encoding="utf-8", errors="replace")
    if MARK in text:
        print("patch-proci: already applied")
        return 0
    hits = 0
    if OLD in text:
        text = text.replace(OLD, NEW, 1)
        hits += 1
    else:
        print("patch-proci: MISS the proc_info wrapper body")
    if DECL not in text:
        i = text.find("#include")
        j = text.find("\n", i)
        text = text[:j + 1] + DECL + text[j + 1:]
    f.write_text(text, encoding="utf-8")
    print(f"patch-proci: probes at {hits}/1 site")
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
